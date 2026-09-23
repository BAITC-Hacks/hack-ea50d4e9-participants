import { useEffect, useState } from 'react'

type Person = { employee_id: string; full_name: string; role: string; grade: string; department: string; preferred_language: string }
type SkillGap = { skill_id: string; name: string; confirmed: number; modelled: number; required: number; gap: number; critical: boolean }
type Progress = {
  target: { role: string; grade: string } | null
  milestone: { role: string; grade: string } | null
  long_term_goal: { role: string; grade: string } | null
  mode: string
  coverage_pct: number | null
  gaps: SkillGap[]
  applied: { skill_id: string; before: number; after: number; event_id: string }[]
  uncertain_record_ids: string[]
  as_of_date: string
}
type History = { event_id: string; event_title: string; date: string; status: string; source?: string }
type Detail = { profile: Person & { hire_date: string; tenure_months: number; work_format: string; career_goal: { target_role: string; target_grade: string } | null; last_review_date: string }; history: History[]; mandatory: { event_id: string; title: string; status: string }[] }
type Impact = { skill_id: string; name: string; before: number; after: number; gain: number; required: number; long_term_required: number; gap_before: number; closes_gap: number; long_term_closes_gap: number; critical: boolean }
type Recommendation = { event_id: string; title: string; description: string; type: string; format: string; duration_hours: number; next_session: string | null; score: number; impacts: Impact[]; reasons: { code: string; text: string }[]; explanation: string; local_rank: number; selection_summary: string }
type BlockedCriticalSkill = { skill_id: string; name: string; current: number; required: number; message: string; events: { event_id: string; title: string; reason_code: string; reason: string }[] }
type RecommendationResponse = { items: Recommendation[]; provider: string; candidate_count: number; excluded: Record<string, number>; blocked_critical: BlockedCriticalSkill[]; duration_ms: number; cached?: boolean }
type HrPerson = { employee_id: string; full_name: string; role: string; grade: string }
type HrData = { employee_count: number; without_step_count: number; without_goal_count: number; goal_met_count: number; gaps: { skill_id: string; name: string; employee_count: number; denominator: number; share_pct: number }[]; without_step: (HrPerson & { reasons: Record<string, number> })[]; without_goal: HrPerson[]; goal_met: HrPerson[]; participation: { event_id: string; title: string; total: number; completed: number; no_show: number; dropped: number; declined: number; overdue: number; completion_pct: number }[]; filters: { roles: string[]; grades: string[]; departments: string[] } }
type Meta = { as_of_date: string; dataset_version: string; batches: { id: string; label: string; employee_count: number; activity_count: number }[]; demo_mode: boolean }

const statusLabel: Record<string, string> = { completed: 'Завершено', in_progress: 'В процессе', dropped: 'Прервано', no_show: 'Пропуск', declined: 'Отказ', overdue: 'Просрочено', not_assigned: 'Не назначено' }
const formatLabel: Record<string, string> = { online: 'Онлайн', offline: 'Очно', self_paced: 'Самостоятельно' }
const exclusionLabel: Record<string, string> = { mandatory: 'обязательное', audience: 'не подходит по роли или грейду', prerequisites: 'не хватает входных навыков', completed: 'уже завершено', in_progress: 'уже в процессе', no_session: 'нет будущей сессии', repeat_cooldown: 'пауза между встречами', no_gain: 'не развивает нужные навыки', no_goal_gain: 'не сокращает разрыв до цели' }

async function api<T>(path: string, role: string, employeeId: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { 'X-Demo-Role': role, 'X-Demo-Employee': employeeId, ...(options.headers || {}) }
  })
  if (!response.ok) {
    let message = `Ошибка ${response.status}`
    try {
      const body = await response.json()
      message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch { /* keep status */ }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

function targetName(target: { role: string; grade: string } | null) {
  return target ? `${target.role} · ${target.grade}` : 'Цель не задана'
}

function Logo() {
  return <div className="brand"><div className="brand-mark"><span>Q</span></div><div><strong>Career Quest</strong><small>ваша траектория роста</small></div></div>
}

function EmptyState({ title, text }: { title: string; text: string }) {
  return <div className="empty-state"><div className="empty-icon">✧</div><h3>{title}</h3><p>{text}</p></div>
}

function Ring({ value }: { value: number | null }) {
  return <div className="ring" style={{ background: value === null ? '#e7eee9' : `conic-gradient(#28ad6f ${value}%, #e7eee9 ${value}% 100%)` }}><div><strong>{value === null ? '—' : `${value}%`}</strong><span>{value === null ? 'цель не задана' : 'покрытие цели'}</span></div></div>
}

export default function App() {
  const [role, setRole] = useState<'employee' | 'hr'>('employee')
  const [view, setView] = useState<'profile' | 'hr' | 'import'>('profile')
  const [people, setPeople] = useState<Person[]>([])
  // In employee mode the server only returns this selected profile; HR mode
  // refreshes the directory and receives the full authorised list.
  const [selected, setSelected] = useState('E0028')
  const [meta, setMeta] = useState<Meta | null>(null)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [recommendations, setRecommendations] = useState<RecommendationResponse | null>(null)
  const [loadingProfile, setLoadingProfile] = useState(false)
  const [loadingRecommendations, setLoadingRecommendations] = useState(false)
  const [busyEvent, setBusyEvent] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [hr, setHr] = useState<HrData | null>(null)
  const [hrFilters, setHrFilters] = useState({ role: '', grade: '', department: '' })
  const [employeeFile, setEmployeeFile] = useState<File | null>(null)
  const [historyFile, setHistoryFile] = useState<File | null>(null)
  const [importLabel, setImportLabel] = useState('Проверочные профили')
  const [importResult, setImportResult] = useState<{ valid?: boolean; errors?: string[]; employee_count?: number; activity_count?: number; batch_id?: string; already_imported?: boolean } | null>(null)
  const [importBusy, setImportBusy] = useState(false)

  async function refreshDirectory(preferredId?: string) {
    const [nextMeta, nextPeople] = await Promise.all([
      api<Meta>('/meta', role, selected),
      api<Person[]>('/employees', role, selected)
    ])
    setMeta(nextMeta)
    setPeople(nextPeople)
    if (preferredId && nextPeople.some(p => p.employee_id === preferredId)) setSelected(preferredId)
    else if (!selected || !nextPeople.some(p => p.employee_id === selected))
      setSelected((nextPeople.find(p => p.employee_id === 'E0028') || nextPeople[0])?.employee_id || '')
  }

  useEffect(() => {
    refreshDirectory(selected).catch(err => setError(err.message))
    // Refresh on role changes so an employee never keeps the HR directory.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [role])

  useEffect(() => {
    if (!selected || view !== 'profile') return
    let active = true
    setError('')
    setLoadingProfile(true)
    setLoadingRecommendations(true)
    setRecommendations(null)
    Promise.all([
      api<Detail>(`/employees/${selected}`, role, selected),
      api<Progress>(`/employees/${selected}/progress`, role, selected)
    ]).then(([nextDetail, nextProgress]) => {
      if (active) { setDetail(nextDetail); setProgress(nextProgress) }
    }).catch(err => { if (active) setError(err.message) })
      .finally(() => { if (active) setLoadingProfile(false) })
    api<RecommendationResponse>(`/employees/${selected}/recommendations`, role, selected)
      .then(data => { if (active) setRecommendations(data) })
      .catch(err => { if (active) setError(err.message) })
      .finally(() => { if (active) setLoadingRecommendations(false) })
    return () => { active = false }
  }, [selected, role, view])

  useEffect(() => {
    if (view !== 'hr' || role !== 'hr') return
    const params = new URLSearchParams()
    Object.entries(hrFilters).forEach(([key, value]) => { if (value) params.set(key, value) })
    api<HrData>(`/hr/overview?${params.toString()}`, role, selected)
      .then(setHr).catch(err => setError(err.message))
  }, [view, role, selected, hrFilters])

  async function complete(eventId: string) {
    if (!selected) return
    setBusyEvent(eventId)
    setError('')
    try {
      await api(`/employees/${selected}/activities/${eventId}/complete`, role, selected, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({})
      })
      setNotice('Демо-завершение записано. Модельный прогресс обновлён.')
      const [nextDetail, nextProgress, nextRecommendations] = await Promise.all([
        api<Detail>(`/employees/${selected}`, role, selected),
        api<Progress>(`/employees/${selected}/progress`, role, selected),
        api<RecommendationResponse>(`/employees/${selected}/recommendations`, role, selected)
      ])
      setDetail(nextDetail); setProgress(nextProgress); setRecommendations(nextRecommendations)
    } catch (err) { setError((err as Error).message) }
    finally { setBusyEvent(null) }
  }

  async function upload(commit: boolean) {
    setImportBusy(true); setError(''); setImportResult(null)
    try {
      const form = new FormData()
      if (employeeFile) form.append('employees_file', employeeFile)
      if (historyFile) form.append('history_file', historyFile)
      if (commit) form.append('label', importLabel)
      const result = await api<typeof importResult>(`/import/${commit ? 'commit' : 'preview'}`, 'hr', selected, { method: 'POST', body: form })
      setImportResult(result)
      if (commit && result?.batch_id) {
        setNotice(result.already_imported ? 'Набор уже был загружен.' : 'Проверочный набор успешно загружен.')
        const imported = employeeFile ? await api<Person[]>(`/employees?batch_id=${result.batch_id}`, 'hr', selected) : []
        const preferred = imported[0]?.employee_id || selected
        await refreshDirectory(preferred)
      }
    } catch (err) { setError((err as Error).message) }
    finally { setImportBusy(false) }
  }

  async function deleteBatch(batchId: string) {
    if (!window.confirm('Удалить этот проверочный набор?')) return
    try {
      await api(`/import/${batchId}`, 'hr', selected, { method: 'DELETE' })
      setNotice('Проверочный набор удалён.')
      await refreshDirectory()
    } catch (err) { setError((err as Error).message) }
  }

  function openEmployee(id: string) {
    setSelected(id); setView('profile'); setNotice('')
  }

  function scrollToSection(selector: string) {
    document.querySelector(selector)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return <div className="app-shell">
    <aside className="sidebar">
      <Logo />
      <div className="sidebar-label">РАБОЧЕЕ ПРОСТРАНСТВО</div>
      <nav className="navigation" aria-label="Разделы">
        <button className={view === 'profile' ? 'active' : ''} onClick={() => setView('profile')}><span>⌂</span> Моя траектория</button>
        {role === 'employee' && <button className="mobile-nav-item" onClick={() => scrollToSection('.recommend-section')}><span>✦</span> Развитие</button>}
        {role === 'employee' && <button className="mobile-nav-item" onClick={() => scrollToSection('.skill-panel')}><span>▤</span> Навыки</button>}
        {role === 'employee' && <button className="mobile-nav-item" onClick={() => scrollToSection('.profile-card')}><span>○</span> Профиль</button>}
        {role === 'hr' && <button className={view === 'hr' ? 'active' : ''} onClick={() => setView('hr')}><span>▥</span> Аналитика HR</button>}
        {role === 'hr' && <button className={view === 'import' ? 'active' : ''} onClick={() => setView('import')}><span>⇧</span> Импорт данных</button>}
      </nav>
      <div className="sidebar-bottom">
        <div className="snapshot"><span className="status-dot" /> Срез данных: {meta?.as_of_date || '—'}</div>
        <div className="mode-card"><div className="mode-icon">◈</div><div><strong>Демо-режим</strong><p>Профили и события синтетические</p></div></div>
      </div>
    </aside>

    <main className="main-area">
      <header className="topbar">
        <div className="topbar-breadcrumb"><b>{view === 'profile' ? 'Моя траектория' : view === 'hr' ? 'Аналитика HR' : 'Импорт данных'}</b><small>Career Quest</small></div>
        <div className="topbar-actions">
          <div className="role-toggle" aria-label="Роль демо"><button className={role === 'employee' ? 'chosen' : ''} onClick={() => { setRole('employee'); setView('profile') }}>Сотрудник</button><button className={role === 'hr' ? 'chosen' : ''} onClick={() => setRole('hr')}>HR</button></div>
          <button className="notification-button" aria-label="Уведомления"><span>●</span>◎</button>
          <div className="avatar">{role === 'hr' ? 'HR' : detail?.profile.full_name?.split(' ').map(x => x[0]).slice(0, 2).join('') || 'CQ'}</div>
        </div>
      </header>

      <div className="content">
        {error && <div className="alert error"><span>!</span>{error}<button onClick={() => setError('')}>×</button></div>}
        {notice && <div className="alert success"><span>✓</span>{notice}<button onClick={() => setNotice('')}>×</button></div>}

        {view === 'profile' && <>
          <div className="page-heading profile-heading"><div><div className="eyebrow">ПЕРСОНАЛЬНАЯ ТРАЕКТОРИЯ</div><h1>Ваш следующий шаг — яснее</h1><p>Навыки, карьерная цель и действия, которые реально приближают к ней.</p></div><div className="heading-visual" aria-hidden="true"><i /><i /><i /><span>↗</span></div><div className="person-picker"><label htmlFor="employee">Профиль для демонстрации</label><select id="employee" value={selected} onChange={e => setSelected(e.target.value)}>{people.map(person => <option key={person.employee_id} value={person.employee_id}>{person.full_name} · {person.employee_id}</option>)}</select></div></div>
          {loadingProfile && !detail ? <div className="loading-card">Загружаем профиль...</div> : detail && progress ? <>
            <section className="hero-grid">
              <div className="profile-card panel"><div className="profile-top"><div className="large-avatar">{detail.profile.full_name.split(' ').map(x => x[0]).slice(0, 2).join('')}</div><div><span className="mini-label">ПРОФИЛЬ СОТРУДНИКА</span><h2>{detail.profile.full_name}</h2><p>{detail.profile.department}</p></div></div><div className="profile-facts"><div><span>Роль</span><strong>{detail.profile.role}</strong></div><div><span>Грейд</span><strong>{detail.profile.grade}</strong></div><div><span>Стаж</span><strong>{detail.profile.tenure_months} мес.</strong></div><div><span>Формат</span><strong>{detail.profile.work_format}</strong></div></div></div>
              <div className="trajectory-card panel"><div className="section-kicker">КАРЬЕРНАЯ КАРТА</div><h2>От текущей роли к следующей вехе</h2><div className="trajectory"><div className="trajectory-node current"><small>СЕЙЧАС</small><strong>{detail.profile.grade}</strong><span>{detail.profile.role}</span></div><div className="trajectory-line"><span>→</span></div><div className="trajectory-node future"><small>{progress.mode === 'maintain' ? 'РАЗВИТИЕ' : 'СЛЕДУЮЩАЯ ВЕХА'}</small><strong>{progress.target?.grade || '—'}</strong><span>{progress.target?.role || 'Цель не задана'}</span></div></div>{progress.long_term_goal && targetName(progress.long_term_goal) !== targetName(progress.milestone) && <p className="long-goal">Долгосрочная цель: <strong>{targetName(progress.long_term_goal)}</strong></p>}</div>
              <div className="progress-card panel"><div className="section-kicker">ГОТОВНОСТЬ ПО НАВЫКАМ</div><Ring value={progress.coverage_pct} />{progress.target ? <p>Покрытие требований к профилю <strong>{targetName(progress.target)}</strong></p> : <p>Карьерная цель не задана. Готовность пока нельзя рассчитать.</p>}<small>Это расчёт навыков, а не решение о повышении.</small></div>
            </section>

            <section className="recommend-section"><div className="section-heading"><div><div className="eyebrow">ПОДОБРАНО ДЛЯ ВАС</div><h2>Рекомендуемые шаги</h2><p>Каждый шаг проверен на доступность и связан с вашей целью.</p></div><div className="source-pill">{loadingRecommendations ? 'Подбираем…' : recommendations?.provider === 'fallback' ? 'Прозрачный алгоритм' : recommendations?.provider === 'openai' ? 'AI · OpenAI' : recommendations?.provider === 'nvidia' ? 'AI · NVIDIA' : 'Анализ данных'}</div></div>
              {!loadingRecommendations && recommendations && recommendations.items.length > 0 && <p className="selection-pool">Из {recommendations.candidate_count} доступных активностей, сокращающих разрыв до цели, {recommendations.provider === 'openai' ? 'OpenAI' : recommendations.provider === 'nvidia' ? 'NVIDIA' : 'локальный алгоритм'} выбрал {recommendations.items.length}. Причины и ожидаемый прирост приведены в каждой карточке.</p>}
              {!loadingRecommendations && recommendations?.blocked_critical.length ? <div className="critical-blockers panel">
                <div className="eyebrow">КРИТИЧНЫЕ РАЗРЫВЫ</div>
                <h3>Почему пока нет шага по критичному навыку</h3>
                <p>Здесь перечислены ограничения каталога. Они не означают, что навык невозможно развивать другими способами.</p>
                {recommendations.blocked_critical.map(gap => <div className="blocked-skill" key={gap.skill_id}>
                  <div className="blocked-skill-heading"><strong>{gap.name}</strong><span>{gap.current} / {gap.required} до цели</span></div>
                  <p>{gap.message}</p>
                  {gap.events.length > 0 && <ul>{gap.events.map(event => <li key={event.event_id}><strong>{event.title}</strong> — {event.reason}</li>)}</ul>}
                </div>)}
              </div> : null}
              {loadingRecommendations ? <div className="recommend-grid"><div className="skeleton-card" /><div className="skeleton-card" /><div className="skeleton-card" /></div> : recommendations?.items.length ? <div className="recommend-grid">{recommendations.items.map((item, index) => <article className="recommend-card panel" key={item.event_id}><div className="card-top"><div className="rank">0{index + 1}</div><span className="event-type">{formatLabel[item.format] || item.format}</span></div><h3>{item.title}</h3><p className="event-description">{item.description}</p><div className="event-meta"><span>◷ {item.duration_hours} ч</span><span>{item.next_session ? `▣ ${item.next_session}` : '↗ В своём темпе'}</span></div><div className="impact-list">{item.impacts.filter(impact => impact.gain > 0).slice(0, 3).map(impact => <div key={impact.skill_id}><span>{impact.name}{impact.critical && <em>критично</em>}</span><strong>{impact.before} → {impact.after}</strong></div>)}</div><div className="why"><span>ПОЧЕМУ ЭТОТ ШАГ</span><p className="selection-summary">{item.selection_summary}</p>{item.reasons.map(reason => <p key={reason.code}><b>✓</b>{reason.text}</p>)}</div><button className="primary-button" disabled={busyEvent !== null} onClick={() => complete(item.event_id)}>{busyEvent === item.event_id ? 'Обновляем…' : 'Смоделировать выполнение'} <span>↗</span></button></article>)}</div> : <EmptyState title="Подходящих шагов пока нет" text={`Система не подбирает недоступные активности. ${Object.entries(recommendations?.excluded || {}).filter(([key]) => key !== 'mandatory').slice(0, 3).map(([key, count]) => `${exclusionLabel[key] || key}: ${count}`).join('; ')}`} />}</section>

            <section className="lower-grid"><div className="panel skill-panel"><div className="section-heading compact"><div><div className="eyebrow">КАРТА КОМПЕТЕНЦИЙ</div><h2>Путь к целевому профилю</h2></div><span className="muted">Оценка от {detail.profile.last_review_date}</span></div><div className="skill-list">{progress.gaps.map(skill => <div className="skill-row" key={skill.skill_id}><div className="skill-name"><strong>{skill.name}</strong>{skill.critical && <span>Критичный</span>}</div><div className="skill-track"><div style={{ width: `${Math.min(100, skill.modelled / Math.max(skill.required, 1) * 100)}%` }} /></div><div className="skill-values"><strong>{skill.modelled}/{skill.required}</strong><small>{skill.gap ? `−${skill.gap} до цели` : 'Достигнуто'}</small></div></div>)}</div><p className="footnote">Подтверждённый уровень — последняя оценка; модельный включает однозначно датированные завершения. {progress.uncertain_record_ids.length > 0 && `Неоднозначных записей self-paced: ${progress.uncertain_record_ids.length}.`}</p></div>
              <div className="right-stack"><div className="panel history-panel"><div className="eyebrow">АКТИВНОСТЬ</div><h2>История участия</h2>{detail.history.length ? <div className="history-list">{detail.history.slice(0, 6).map((row, index) => <div key={`${row.event_id}-${row.date}-${index}`} className="history-row"><div className="history-icon">{row.status === 'completed' ? '✓' : '·'}</div><div><strong>{row.event_title}</strong><span>{row.date}</span></div><span className={`history-status ${row.status}`}>{statusLabel[row.status] || row.status}</span></div>)}</div> : <p className="muted">Записей пока нет.</p>}</div><div className="panel mandatory-panel"><div className="eyebrow">ОТДЕЛЬНО ОТ РЕКОМЕНДАЦИЙ</div><h2>Обязательные активности</h2>{detail.mandatory.map(item => <div className="mandatory-row" key={item.event_id}><span>{item.title}</span><small>{statusLabel[item.status] || item.status}</small></div>)}</div></div></section>
          </> : !loadingProfile && <EmptyState title="Профиль не найден" text="Выберите другого сотрудника или загрузите проверочный набор." />}
        </>}

        {view === 'hr' && role === 'hr' && <><div className="page-heading"><div><div className="eyebrow">ОБЗОР РАЗВИТИЯ</div><h1>Аналитика компетенций</h1><p>Где есть разрывы, как идут активности и кому пока нечего предложить.</p></div></div><div className="filter-bar"><select value={hrFilters.role} onChange={e => setHrFilters({ ...hrFilters, role: e.target.value })}><option value="">Все роли</option>{hr?.filters.roles.map(x => <option key={x}>{x}</option>)}</select><select value={hrFilters.grade} onChange={e => setHrFilters({ ...hrFilters, grade: e.target.value })}><option value="">Все грейды</option>{hr?.filters.grades.map(x => <option key={x}>{x}</option>)}</select><select value={hrFilters.department} onChange={e => setHrFilters({ ...hrFilters, department: e.target.value })}><option value="">Все подразделения</option>{hr?.filters.departments.map(x => <option key={x}>{x}</option>)}</select></div>{hr ? <><div className="metrics-grid"><div className="metric panel"><span>СОТРУДНИКИ</span><strong>{hr.employee_count}</strong><small>в выбранном срезе</small></div><div className="metric panel"><span>РАЗРЫВ БЕЗ ДОСТУПНОГО ШАГА</span><strong>{hr.without_step_count}</strong><small>нужно расширить каталог</small></div><div className="metric panel"><span>ЦЕЛЬ НЕ ЗАДАНА</span><strong>{hr.without_goal_count}</strong><small>нужно обсудить траекторию</small></div><div className="metric panel"><span>НАВЫКИ С РАЗРЫВОМ</span><strong>{hr.gaps.length}</strong><small>разных компетенций</small></div></div><div className="hr-grid"><div className="panel hr-panel"><div className="eyebrow">КАРТА РАЗРЫВОВ</div><h2>Чаще всего требуют развития</h2>{hr.gaps.slice(0, 12).map(item => <div className="gap-row" key={item.skill_id}><div><strong>{item.name}</strong><span>{item.employee_count} из {item.denominator}</span></div><div className="gap-bar"><i style={{ width: `${item.share_pct}%` }} /></div><b>{item.share_pct}%</b></div>)}</div><div className="panel hr-panel"><div className="eyebrow">НУЖЕН НОВЫЙ ВАРИАНТ</div><h2>Есть разрыв, но нет шага</h2>{hr.without_step.length ? <div className="hr-person-list">{hr.without_step.map(item => <button className="person-row" key={item.employee_id} onClick={() => openEmployee(item.employee_id)}><span className="person-initials">{item.full_name.split(' ').map(x => x[0]).slice(0, 2).join('')}</span><span><strong>{item.full_name}</strong><small>{item.role} · {item.grade}</small></span><b>↗</b></button>)}</div> : <EmptyState title="Доступные шаги есть" text="Для каждого сотрудника с разрывом найден подходящий шаг." />}<div className="hr-subsection"><h3>Карьерная цель не задана</h3><p>Этим сотрудникам сначала нужна следующая цель. Достигли заданной цели: {hr.goal_met_count}.</p>{hr.without_goal.length ? <div className="hr-person-list">{hr.without_goal.map(item => <button className="person-row" key={item.employee_id} onClick={() => openEmployee(item.employee_id)}><span className="person-initials">{item.full_name.split(' ').map(x => x[0]).slice(0, 2).join('')}</span><span><strong>{item.full_name}</strong><small>{item.role} · {item.grade}</small></span><b>↗</b></button>)}</div> : <p>Все сотрудники имеют карьерную цель.</p>}</div></div></div><div className="panel hr-panel participation"><div className="eyebrow">УЧАСТИЕ ПО АКТИВНОСТЯМ</div><h2>Как проходят программы</h2><div className="table-wrap"><table><thead><tr><th>Активность</th><th>Участий</th><th>Завершено</th><th>Пропуски</th><th>Отказы</th><th>Доля завершения</th></tr></thead><tbody>{hr.participation.map(item => <tr key={item.event_id}><td>{item.title}</td><td>{item.total}</td><td>{item.completed}</td><td>{item.no_show}</td><td>{item.declined}</td><td><span className="table-percent">{item.completion_pct}%</span></td></tr>)}</tbody></table></div><p className="footnote">Доля завершения = завершённые записи / все записи участия в выбранном срезе. Это не индивидуальный рейтинг.</p></div></> : <div className="loading-card">Считаем агрегаты…</div>}</>}

        {view === 'import' && role === 'hr' && <><div className="page-heading"><div><div className="eyebrow">ПРОВЕРОЧНЫЕ ДАННЫЕ</div><h1>Импорт профилей и истории</h1><p>Добавьте новые профили жюри без изменения исходного набора.</p></div></div><div className="import-grid"><div className="panel import-panel"><h2>Новый набор</h2><p>Можно загрузить один JSON-профиль, объект с `employees[]` или массив. История — CSV той же схемы, что в стартовом наборе.</p><label className="field-label">Название набора<input value={importLabel} onChange={e => setImportLabel(e.target.value)} maxLength={120} /></label><label className="upload-box"><span>01</span><strong>{employeeFile?.name || 'Выбрать employees.json'}</strong><small>JSON · до 3 МБ</small><input type="file" accept=".json,application/json" onChange={e => setEmployeeFile(e.target.files?.[0] || null)} /></label><label className="upload-box"><span>02</span><strong>{historyFile?.name || 'Выбрать activity_history.csv'}</strong><small>CSV · до 3 МБ</small><input type="file" accept=".csv,text/csv" onChange={e => setHistoryFile(e.target.files?.[0] || null)} /></label><div className="import-actions"><button className="secondary-button" disabled={importBusy || (!employeeFile && !historyFile)} onClick={() => upload(false)}>Проверить файлы</button><button className="primary-button" disabled={importBusy || (!employeeFile && !historyFile) || importResult?.valid === false} onClick={() => upload(true)}>{importBusy ? 'Обрабатываем…' : 'Загрузить набор'} ↗</button></div>{importResult && <div className={`import-result ${importResult.errors?.length ? 'bad' : 'good'}`}>{importResult.errors?.length ? <><strong>Найдены ошибки</strong>{importResult.errors.map((item, index) => <p key={index}>{item}</p>)}</> : <><strong>{importResult.batch_id ? 'Набор загружен' : 'Проверка пройдена'}</strong><p>Сотрудников: {importResult.employee_count ?? '—'} · записей истории: {importResult.activity_count ?? '—'}</p></>}</div>}</div><div className="panel batches-panel"><div className="eyebrow">ХРАНЕНИЕ</div><h2>Загруженные наборы</h2>{meta?.batches.map(batch => <div className="batch-row" key={batch.id}><div className="batch-symbol">▣</div><div><strong>{batch.label}</strong><small>{batch.employee_count} профилей · {batch.activity_count} записей</small></div>{batch.id !== 'base' && <button onClick={() => deleteBatch(batch.id)}>Удалить</button>}</div>)}<p className="footnote">Повторная загрузка того же набора не создаёт дубликаты. Исходные файлы не перезаписываются.</p></div></div></>}
      </div>
    </main>
  </div>
}
