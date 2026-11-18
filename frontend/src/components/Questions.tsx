import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'react-router-dom'
import { api, json } from '../api/client'
import { errorMessage } from '../lib/errors'
import { formatDateTime } from '../lib/format'
import { Icon } from '../ui/icons'

interface Question {
  id: string
  body: string
  asker_name: string
  mine: boolean
  answer: string | null
  answered_at: string | null
  created_at: string
}

interface QuestionPage {
  items: Question[]
  unanswered: number
  next_before: string | null
}

function AnswerForm({ question, onDone }: { question: Question; onDone: () => void }) {
  const { t } = useTranslation()
  const [text, setText] = useState('')
  const answer = useMutation({
    mutationFn: () => api(`/api/questions/${question.id}/answer`, { method: 'POST', body: json({ body: text.trim() }) }),
    onSuccess: onDone,
  })
  return (
    <form className="mt-2 space-y-2" onSubmit={(e) => { e.preventDefault(); answer.mutate() }}>
      <label className="sr-only" htmlFor={`answer-${question.id}`}>{t('questions.answerLabel')}</label>
      <textarea id={`answer-${question.id}`} className="input min-h-20" maxLength={1000} value={text} onChange={(e) => setText(e.target.value)} placeholder={t('questions.answerPlaceholder')} />
      {answer.isError && <p role="alert" className="text-sm text-danger">{errorMessage(answer.error, t)}</p>}
      <button className="btn-primary min-h-10 text-sm" disabled={!text.trim() || answer.isPending}>{t('questions.answer')}</button>
    </form>
  )
}

/** Public questions and answers on a listing. */
export function Questions({ listingId, isSeller, signedIn }: { listingId: string; isSeller: boolean; signedIn: boolean }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const location = useLocation()
  const key = ['questions', listingId]
  const questions = useQuery({ queryKey: key, queryFn: () => api<QuestionPage>(`/api/listings/${listingId}/questions`) })
  const refresh = () => void qc.invalidateQueries({ queryKey: key })
  const [text, setText] = useState('')
  const ask = useMutation({
    mutationFn: () => api(`/api/listings/${listingId}/questions`, { method: 'POST', body: json({ body: text.trim() }) }),
    onSuccess: () => { setText(''); refresh() },
  })
  const hide = useMutation({ mutationFn: (id: string) => api(`/api/questions/${id}`, { method: 'DELETE' }), onSuccess: refresh })

  function submit(e: FormEvent) {
    e.preventDefault()
    ask.mutate()
  }
  const items = questions.data?.items ?? []

  return (
    <section className="card space-y-4 p-5" aria-labelledby="questions-title">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="questions-title" className="font-semibold">{t('questions.title')}</h2>
        {isSeller && (questions.data?.unanswered ?? 0) > 0 && (
          <span className="text-xs font-semibold text-warning-fg">{t('questions.unanswered', { count: questions.data?.unanswered })}</span>
        )}
      </div>

      {!isSeller && (signedIn ? (
        <form onSubmit={submit} className="space-y-2">
          <label className="sr-only" htmlFor="question">{t('questions.askLabel')}</label>
          <div className="flex gap-2">
            <input id="question" className="input flex-1" maxLength={500} value={text} onChange={(e) => setText(e.target.value)} placeholder={t('questions.placeholder')} />
            <button className="btn-primary shrink-0" disabled={text.trim().length < 3 || ask.isPending}>{t('questions.ask')}</button>
          </div>
          {ask.isError && <p role="alert" className="text-sm text-danger">{errorMessage(ask.error, t)}</p>}
          <p className="text-xs text-muted">{t('questions.publicHint')}</p>
        </form>
      ) : (
        <p className="text-sm text-muted">
          <Link to="/login" state={{ from: location.pathname }} className="font-semibold text-primary hover:underline">{t('nav.signIn')}</Link>{' '}
          {t('questions.signInToAsk')}
        </p>
      ))}

      {questions.isSuccess && items.length === 0 && <p className="text-sm text-muted">{t('questions.none')}</p>}
      <ul className="space-y-4">
        {items.map((q) => (
          <li key={q.id} className="space-y-1.5 text-sm">
            <p className="flex items-start gap-2">
              <Icon name="chat" size={16} className="mt-0.5 shrink-0 text-muted" />
              <span className="flex-1">
                {q.body}
                <span className="ml-1 text-xs text-muted">· {q.asker_name} · {formatDateTime(q.created_at)}</span>
              </span>
              {(isSeller || (q.mine && !q.answer)) && (
                <button type="button" className="text-xs text-muted hover:text-danger" disabled={hide.isPending} onClick={() => hide.mutate(q.id)}>
                  {t('questions.remove')}
                </button>
              )}
            </p>
            {q.answer ? (
              <p className="ml-6 border-l-2 border-primary pl-3 text-muted">{q.answer}</p>
            ) : isSeller ? (
              <div className="ml-6"><AnswerForm question={q} onDone={refresh} /></div>
            ) : (
              <p className="ml-6 text-xs text-muted">{t('questions.waiting')}</p>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
