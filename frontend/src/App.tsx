import { FormEvent, useCallback, useEffect, useState } from 'react'

type DemoRole = 'employee' | 'hr'

type EmployeeSummary = {
  employee_id: string
  full_name: string
  role: string
  grade: string
  department: string
}

type ProfileResponse = {
  profile: EmployeeSummary & {
    tenure_months: number
    work_format: string
    preferred_language: string
  }
  history: Array<{ event_id: string; event_title: string; date: string; status: string }>
  mandatory: Array<{ event_id: string; title: string; status: string }>
}

type Gap = {
  skill_id: string
  name: string
  confirmed: number
  modelled: number
  required: number
  gap: number
  critical: boolean
}

type Progress = {
  as_of_date: string
  target: { role: string; grade: string } | null
  coverage_pct: number
  gaps: Gap[]
  applied: Array<{ event_id: string; skill_id: string; before: number; after: number }>
}

type Recommendation = {
  event_id: string
  title: string
  description: string
  type: string
  format: string
  duration_hours: number
  score: number
  explanation: string
  impacts: Array<{
    skill_id: string
    name: string
    before: number
    after: number
    required: number
    gain: number
    critical: boolean
  }>
}

type RecommendationsResponse = {
  items: Recommendation[]
  provider: string
  candidate_count: number
}

type HrOverview = {
  employee_count: number
  without_step_count: number
  gaps: Array<{ skill_id: string; name: string; employee_count: number; denominator: number; share_pct: number }>
  without_step: Array<{ employee_id: string; full_name: string; role: string; grade: string }>
  participation: Array<{
    event_id: string
    title: string
    total: number
    completed: number
    no_show: number
    dropped: number
    declined: number
    overdue: number
    completion_pct: number
  }>
  filters: { roles: string[]; grades: string[]; departments: string[] }
}

type ImportPreview = {
  employee_count: number
  activity_count: number
  valid: boolean
  errors: string[]
  batch_id?: string
  already_imported?: boolean
}

async function request<T>(path: string, role: DemoRole, employeeId: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  headers.set('x-demo-role', role)
  headers.set('x-demo-employee', employeeId)
  if (init?.body && !(init.body instanceof FormData)) headers.set('content-type', 'application/json')
  const response = await fetch(path, { ...init, headers })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText })) as { detail?: unknown }
    const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

function StatusPill({ children, tone = 'neutral' }: { children: string; tone?: 'neutral' | 'good' | 'warn' }) {
  return <span className={`pill pill-${tone}`}>{children}</span>
}

function App() {
  const [role, setRole] = useState<DemoRole>('employee')
  const [employeeId, setEmployeeId] = useState('E0002')
  const [employees, setEmployees] = useState<EmployeeSummary[]>([])
  const [profile, setProfile] = useState<ProfileResponse | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [recommendations, setRecommendations] = useState<RecommendationsResponse | null>(null)
  const [overview, setOverview] = useState<HrOverview | null>(null)
  const [filters, setFilters] = useState({ role: '', grade: '', department: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const loadEmployee = useCallback(async () => {
    if (!employeeId.trim()) return
    setBusy(true)
    setError('')
    try {
      const [detail, state, next] = await Promise.all([
        request<ProfileResponse>(`/api/employees/${encodeURIComponent(employeeId)}`, role, employeeId),
        request<Progress>(`/api/employees/${encodeURIComponent(employeeId)}/progress`, role, employeeId),
        request<RecommendationsResponse>(`/api/employees/${encodeURIComponent(employeeId)}/recommendations`, role, employeeId),
      ])
      setProfile(detail)
      setProgress(state)
      setRecommendations(next)
    } catch (cause) {
      setProfile(null)
      setProgress(null)
      setRecommendations(null)
      setError(cause instanceof Error ? cause.message : 'Не удалось загрузить профиль')
    } finally {
      setBusy(false)
    }
  }, [employeeId, role])

  const loadOverview = useCallback(async () => {
    setBusy(true)
    setError('')
    try {
      const query = new URLSearchParams()
      if (filters.role) query.set('role', filters.role)
      if (filters.grade) query.set('grade', filters.grade)
      if (filters.department) query.set('department', filters.department)
      const [people, summary] = await Promise.all([
        request<EmployeeSummary[]>('/api/employees', 'hr', employeeId),
        request<HrOverview>(`/api/hr/overview?${query}`, 'hr', employeeId),
      ])
      setEmployees(people)
      setOverview(summary)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось загрузить HR-дашборд')
    } finally {
      setBusy(false)
    }
  }, [employeeId, filters])

  useEffect(() => {
    if (role === 'hr') void loadOverview()
    else void loadEmployee()
  }, [loadEmployee, loadOverview, role])

  async function complete(item: Recommendation) {
    setBusy(true)
    setError('')
    try {
      await request(`/api/employees/${encodeURIComponent(employeeId)}/activities/${encodeURIComponent(item.event_id)}/complete`, role, employeeId, {
        method: 'POST', body: JSON.stringify({}),
      })
      await loadEmployee()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось завершить активность')
      setBusy(false)
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand"><span className="brand-mark">CQ</span><div><strong>Career Quest</strong><small>Следующий шаг, основанный на данных</small></div></div>
        <div className="role-switch" aria-label="Демо-роль">
          <button className={role === 'employee' ? 'active' : ''} onClick={() => setRole('employee')}>Сотрудник</button>
          <button className={role === 'hr' ? 'active' : ''} onClick={() => setRole('hr')}>HR</button>
        </div>
      </header>

      <main>
        <section className="control-bar">
          {role === 'hr' ? (
            <label>Профиль для просмотра
              <select value={employeeId} onChange={(event) => setEmployeeId(event.target.value)}>
                {employees.map((person) => <option key={person.employee_id} value={person.employee_id}>{person.full_name} · {person.grade}</option>)}
              </select>
            </label>
          ) : (
            <form onSubmit={(event) => { event.preventDefault(); void loadEmployee() }}>
              <label>Ваш демо-ID <input value={employeeId} onChange={(event) => setEmployeeId(event.target.value)} /></label>
              <button className="secondary" type="submit">Открыть</button>
            </form>
          )}
          <StatusPill tone={busy ? 'warn' : 'good'}>{busy ? 'Обновление…' : 'Данные актуальны'}</StatusPill>
        </section>

        {error && <div className="error" role="alert"><strong>Не удалось выполнить запрос.</strong> {error}</div>}
        {role === 'employee' ? (
          <EmployeeView profile={profile} progress={progress} recommendations={recommendations} busy={busy} onComplete={complete} />
        ) : (
          <HrView overview={overview} filters={filters} setFilters={setFilters} onApply={loadOverview} onOpenEmployee={(id) => { setEmployeeId(id); setRole('employee') }} employeeId={employeeId} />
        )}
      </main>
      <footer>Демо-режим · персональные данные защищены серверными проверками доступа</footer>
    </div>
  )
}

function EmployeeView({ profile, progress, recommendations, busy, onComplete }: {
  profile: ProfileResponse | null
  progress: Progress | null
  recommendations: RecommendationsResponse | null
  busy: boolean
  onComplete: (item: Recommendation) => Promise<void>
}) {
  if (!profile || !progress || !recommendations) return <section className="empty">Выберите корректный ID сотрудника.</section>
  return <>
    <section className="hero-card">
      <div>
        <span className="eyebrow">Личная траектория</span>
        <h1>{profile.profile.full_name}</h1>
        <p>{profile.profile.role} · {profile.profile.grade} · стаж {profile.profile.tenure_months} мес.</p>
      </div>
      <div className="progress-ring" style={{ '--value': `${progress.coverage_pct * 3.6}deg` } as React.CSSProperties}>
        <div><strong>{progress.coverage_pct}%</strong><span>готовность</span></div>
      </div>
      <div className="target-box">
        <small>Цель</small>
        {progress.target ? <><strong>{progress.target.grade}</strong><span>{progress.target.role}</span></> : <strong>Траектория не определена</strong>}
        <small>на {progress.as_of_date}</small>
      </div>
    </section>

    <div className="section-heading"><div><span className="eyebrow">Персональный маршрут</span><h2>Следующие шаги</h2></div><StatusPill>{recommendations.provider === 'fallback' ? 'Надёжный fallback' : recommendations.provider}</StatusPill></div>
    {recommendations.items.length ? <div className="recommendation-grid">
      {recommendations.items.map((item, index) => <article className="recommendation" key={item.event_id}>
        <div className="recommendation-top"><span className="rank">0{index + 1}</span><StatusPill tone={index === 0 ? 'good' : 'neutral'}>{item.format}</StatusPill></div>
        <h3>{item.title}</h3><p>{item.description}</p>
        <div className="meta-row"><span>{item.duration_hours} ч</span><span>{item.type}</span><span>score {item.score}</span></div>
        <ul className="impact-list">{item.impacts.filter((impact) => impact.gain > 0).slice(0, 3).map((impact) => <li key={impact.skill_id}><span>{impact.name}{impact.critical && ' ★'}</span><strong>{impact.before} → {impact.after}</strong></li>)}</ul>
        <p className="explanation">{item.explanation}</p>
        <button disabled={busy} onClick={() => void onComplete(item)}>Отметить выполненной</button>
      </article>)}
    </div> : <section className="empty"><h3>Подходящих активностей пока нет</h3><p>Все доступные шаги завершены либо не меняют навыки текущей траектории.</p></section>}

    <div className="two-column">
      <section className="panel"><div className="section-heading"><h2>Разрывы по навыкам</h2><span>{progress.gaps.filter((gap) => gap.gap > 0).length} активных</span></div>
        <div className="gap-list">{progress.gaps.map((gap) => <div className="gap-row" key={gap.skill_id}><div><strong>{gap.name}{gap.critical && ' ★'}</strong><small>подтверждено {gap.confirmed} · модель {gap.modelled} · цель {gap.required}</small></div><div className="mini-bar"><i style={{ width: `${Math.min(100, gap.modelled / gap.required * 100)}%` }} /></div><b>{gap.gap ? `−${gap.gap}` : '✓'}</b></div>)}</div>
      </section>
      <section className="panel"><div className="section-heading"><h2>История</h2><span>последние события</span></div>
        <div className="history-list">{profile.history.slice(0, 8).map((item, index) => <div key={`${item.event_id}-${item.date}-${index}`}><i className={`dot dot-${item.status}`} /><span><strong>{item.event_title}</strong><small>{item.date} · {item.status}</small></span></div>)}</div>
        {profile.mandatory.length > 0 && <><div className="section-heading subheading"><h2>Обязательные активности</h2></div><div className="history-list">{profile.mandatory.map((item) => <div key={item.event_id}><i className={`dot dot-${item.status}`} /><span><strong>{item.title}</strong><small>{item.status}</small></span></div>)}</div></>}
      </section>
    </div>
  </>
}

function HrView({ overview, filters, setFilters, onApply, onOpenEmployee, employeeId }: {
  overview: HrOverview | null
  filters: { role: string; grade: string; department: string }
  setFilters: (value: { role: string; grade: string; department: string }) => void
  onApply: () => Promise<void>
  onOpenEmployee: (id: string) => void
  employeeId: string
}) {
  if (!overview) return <section className="empty">HR-данные загружаются…</section>
  return <>
    <section className="filter-panel">
      <label>Роль<select value={filters.role} onChange={(event) => setFilters({ ...filters, role: event.target.value })}><option value="">Все</option>{overview.filters.roles.map((item) => <option key={item}>{item}</option>)}</select></label>
      <label>Грейд<select value={filters.grade} onChange={(event) => setFilters({ ...filters, grade: event.target.value })}><option value="">Все</option>{overview.filters.grades.map((item) => <option key={item}>{item}</option>)}</select></label>
      <label>Подразделение<select value={filters.department} onChange={(event) => setFilters({ ...filters, department: event.target.value })}><option value="">Все</option>{overview.filters.departments.map((item) => <option key={item}>{item}</option>)}</select></label>
      <button onClick={() => void onApply()}>Применить</button>
    </section>
    <div className="metric-grid"><article><span>Сотрудников</span><strong>{overview.employee_count}</strong></article><article><span>Без следующего шага</span><strong>{overview.without_step_count}</strong></article><article><span>Доля без шага</span><strong>{overview.employee_count ? Math.round(overview.without_step_count / overview.employee_count * 100) : 0}%</strong></article></div>
    <div className="two-column">
      <section className="panel"><div className="section-heading"><h2>Частые дефициты</h2><span>по выбранной группе</span></div><div className="gap-list">{overview.gaps.slice(0, 12).map((gap) => <div className="gap-row" key={gap.skill_id}><div><strong>{gap.name}</strong><small>{gap.employee_count} из {gap.denominator} сотрудников</small></div><div className="mini-bar"><i style={{ width: `${gap.share_pct}%` }} /></div><b>{gap.share_pct}%</b></div>)}</div></section>
      <section className="panel"><div className="section-heading"><h2>Без рекомендации</h2><span>{overview.without_step_count}</span></div>{overview.without_step.length ? <div className="people-list">{overview.without_step.map((person) => <button key={person.employee_id} onClick={() => onOpenEmployee(person.employee_id)}><span><strong>{person.full_name}</strong><small>{person.role} · {person.grade}</small></span><b>Открыть →</b></button>)}</div> : <div className="empty compact">У всех есть доступный шаг.</div>}</section>
    </div>
    <section className="panel"><div className="section-heading"><h2>Участие по активностям</h2><span>история + завершения в приложении</span></div><div className="table-wrap"><table><thead><tr><th>Активность</th><th>Всего</th><th>Завершено</th><th>Пропуски</th><th>Отказы</th><th>Доля</th></tr></thead><tbody>{overview.participation.slice(0, 15).map((item) => <tr key={item.event_id}><td>{item.title}</td><td>{item.total}</td><td>{item.completed}</td><td>{item.no_show}</td><td>{item.dropped + item.declined + item.overdue}</td><td><strong>{item.completion_pct}%</strong></td></tr>)}</tbody></table></div></section>
    <ImportPanel employeeId={employeeId} onImported={onApply} />
  </>
}

function ImportPanel({ employeeId, onImported }: { employeeId: string; onImported: () => Promise<void> }) {
  const [employeesFile, setEmployeesFile] = useState<File | null>(null)
  const [historyFile, setHistoryFile] = useState<File | null>(null)
  const [result, setResult] = useState<ImportPreview | null>(null)
  const [error, setError] = useState('')

  async function submit(event: FormEvent, commit: boolean) {
    event.preventDefault()
    const body = new FormData()
    if (employeesFile) body.append('employees_file', employeesFile)
    if (historyFile) body.append('history_file', historyFile)
    if (commit) body.append('label', 'Проверочный набор')
    setError('')
    try {
      const response = await request<ImportPreview>(`/api/import/${commit ? 'commit' : 'preview'}`, 'hr', employeeId, { method: 'POST', body })
      setResult(response)
      if (commit) await onImported()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Ошибка импорта')
    }
  }

  return <section className="panel import-panel"><div className="section-heading"><div><span className="eyebrow">Контроль данных</span><h2>Проверочный импорт</h2></div></div><form onSubmit={(event) => void submit(event, false)}><label>Профили JSON<input type="file" accept=".json,application/json" onChange={(event) => setEmployeesFile(event.target.files?.[0] ?? null)} /></label><label>История CSV<input type="file" accept=".csv,text/csv" onChange={(event) => setHistoryFile(event.target.files?.[0] ?? null)} /></label><button className="secondary" type="submit">Проверить</button><button type="button" disabled={!result?.valid} onClick={(event) => void submit(event as unknown as FormEvent, true)}>Импортировать</button></form>{error && <div className="error">{error}</div>}{result && <div className={result.valid ? 'import-result valid' : 'import-result invalid'}><strong>{result.valid ? 'Набор валиден' : 'Найдены ошибки'}</strong><span>Профили: {result.employee_count} · история: {result.activity_count}</span>{result.errors?.map((item) => <small key={item}>{item}</small>)}</div>}</section>
}

export default App
