import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { GraduationCap, CheckCircle2, XCircle, ChevronRight, RefreshCw } from 'lucide-react'
import { reviewApi } from '../api/review'
import type { ReviewItem, ReviewQuestion } from '../types/api'
import EmptyState from '../components/common/EmptyState'

export default function DailyReview() {
  const { t } = useTranslation()
  const [items, setItems] = useState<ReviewItem[]>([])
  const [loading, setLoading] = useState(true)
  const [currentIndex, setCurrentIndex] = useState(0)
  const [quizNotes, setQuizNotes] = useState<Record<string, ReviewQuestion>>({})
  const [selectedAnswer, setSelectedAnswer] = useState<string | null>(null)
  const [showResult, setShowResult] = useState(false)
  const [doneCount, setDoneCount] = useState(0)
  const [completed, setCompleted] = useState(false)
  const [questionLoading, setQuestionLoading] = useState(false)

  useEffect(() => {
    reviewApi.today().then((data) => {
      setItems(data.reviews || [])
    }).catch(() => {
      toast.error('加载复习内容失败')
    }).finally(() => setLoading(false))
  }, [])

  const current = items[currentIndex]
  const currentQuestion = current ? quizNotes[current.note_id] : null
  const showQuiz = currentQuestion != null || questionLoading

  const handleStartQuiz = async (noteId: string) => {
    if (quizNotes[noteId]) {
      setSelectedAnswer(null)
      setShowResult(false)
      return
    }
    setQuestionLoading(true)
    try {
      const q = await reviewApi.getQuestion(noteId)
      if (q) {
        setQuizNotes((prev) => ({ ...prev, [noteId]: q }))
      }
    } catch {
      toast.error('获取题目失败')
    }
    setQuestionLoading(false)
  }

  const handleAnswer = (answer: string) => {
    setSelectedAnswer(answer)
    setShowResult(true)
  }

  const advance = (markDone: boolean) => {
    if (markDone) {
      reviewApi.markDone(current.note_id).catch(() => {})
      setDoneCount((c) => c + 1)
    }
    if (currentIndex >= items.length - 1) {
      setCompleted(true)
      return
    }
    setSelectedAnswer(null)
    setShowResult(false)
    setCurrentIndex((i) => i + 1)
  }

  const handleRegenerate = async () => {
    setQuizNotes((prev) => { const next = { ...prev }; delete next[current.note_id]; return next })
    setSelectedAnswer(null)
    setShowResult(false)
    setQuestionLoading(true)
    try {
      const q = await reviewApi.getQuestion(current.note_id)
      if (q) {
        setQuizNotes((prev) => ({ ...prev, [current.note_id]: q }))
      }
    } catch {
      toast.error('重新生成题目失败')
    }
    setQuestionLoading(false)
  }

  const handleConfirm = () => advance(selectedAnswer === currentQuestion?.answer)
  const handleDoneThenNext = () => advance(true)

  return (
    <div className="max-w-2xl mx-auto py-8 px-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-heading text-xl font-semibold text-[var(--color-text)]">{t('review.title')}</h1>
        <div className="flex items-center gap-3">
          {!loading && items.length > 0 && (
            <span className="text-xs text-[var(--color-text-tertiary)]">{t('review.progress')}: {doneCount}/{items.length}</span>
          )}
        </div>
      </div>

      {loading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-32 bg-[var(--color-bg-tertiary)] rounded-lg animate-pulse" />
          ))}
        </div>
      ) : completed ? (
        <div className="bg-[var(--color-card)] rounded-lg border border-[var(--color-border)] p-8 text-center">
          <GraduationCap size={48} className="mx-auto mb-4 text-[var(--color-success)]" />
          <p className="text-base font-medium text-[var(--color-text)] mb-2">{t('review.allDone')}</p>
          <p className="text-sm text-[var(--color-text-tertiary)]">{t('review.progress')}: {doneCount}/{items.length}</p>
        </div>
      ) : items.length === 0 ? (
        <EmptyState icon={<GraduationCap size={48} />} message={t('review.empty')} />
      ) : (
        <div className="space-y-4">
          {!showQuiz ? (
            <div className="bg-[var(--color-card)] rounded-lg border border-[var(--color-border)] p-6">
              <h3 className="text-base font-medium text-[var(--color-text)] mb-3">{current?.title}</h3>
              <p className="text-xs text-[var(--color-text-tertiary)] mb-4">
                {t('review.today')} | {current?.review_count || 0}次回顾
              </p>
              <button
                onClick={() => handleStartQuiz(current.note_id)}
                className="flex items-center gap-2 px-4 py-2 text-sm rounded-md bg-[var(--color-accent)] text-white hover:bg-blue-700 transition-colors"
              >
                {t('review.question')}
                <ChevronRight size={14} />
              </button>
            </div>
          ) : questionLoading ? (
            <div className="bg-[var(--color-card)] rounded-lg border border-[var(--color-border)] p-6 space-y-4">
              <div className="h-4 w-3/4 bg-[var(--color-bg-tertiary)] rounded animate-pulse" />
              <div className="space-y-2">
                {[1, 2, 3, 4].map((i) => (
                  <div key={i} className="h-10 bg-[var(--color-bg-tertiary)] rounded animate-pulse" />
                ))}
              </div>
            </div>
          ) : (
            <div className="bg-[var(--color-card)] rounded-lg border border-[var(--color-border)] p-6 space-y-4">
              <h3 className="text-sm font-medium text-[var(--color-text)]">{currentQuestion?.question}</h3>
              <div className="space-y-2">
                {(currentQuestion?.choices ?? []).map((opt, i) => {
                  const isSelected = selectedAnswer === opt
                  const isCorrectAnswer = opt === currentQuestion?.answer
                  let className = 'w-full text-left px-4 py-3 rounded-md border text-sm transition-colors '
                  if (!showResult) {
                    className += 'border-[var(--color-border)] hover:border-[var(--color-accent)] cursor-pointer'
                  } else if (isCorrectAnswer) {
                    className += 'border-[var(--color-success)] bg-[var(--color-success-bg)] text-[var(--color-success)]'
                  } else if (isSelected && !isCorrectAnswer) {
                    className += 'border-[var(--color-danger)] bg-[var(--color-danger-bg)] text-[var(--color-danger)]'
                  } else {
                    className += 'border-[var(--color-border)] opacity-50'
                  }
                  return (
                    <button key={i} className={className} onClick={() => !showResult && handleAnswer(opt)} disabled={showResult}>
                      <span className="text-xs text-[var(--color-text-tertiary)] mr-2">{String.fromCharCode(65 + i)}.</span>
                      {opt}
                      {showResult && isCorrectAnswer && <CheckCircle2 size={14} className="inline ml-2" />}
                      {showResult && isSelected && !isCorrectAnswer && <XCircle size={14} className="inline ml-2" />}
                    </button>
                  )
                })}
              </div>

              {showResult && (
                <div className={`px-4 py-3 rounded-md text-sm ${selectedAnswer === currentQuestion?.answer ? 'bg-[var(--color-success-bg)] text-[var(--color-success)]' : 'bg-[var(--color-danger-bg)] text-[var(--color-danger)]'}`}>
                  {selectedAnswer === currentQuestion?.answer ? t('review.correct') : t('review.wrong')}
                </div>
              )}

              {showResult && (
                <button
                  onClick={handleRegenerate}
                  disabled={questionLoading}
                  className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-md border border-[var(--color-border)] text-[var(--color-text-tertiary)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-secondary)] disabled:opacity-50 transition-colors"
                >
                  <RefreshCw size={12} className={questionLoading ? 'animate-spin' : ''} />
                  {t('review.regenerate')}
                </button>
              )}

              {showResult && currentIndex < items.length - 1 && (
                <div className="flex justify-between items-center">
                  <button
                    onClick={handleDoneThenNext}
                    className="flex items-center gap-2 px-4 py-2 text-sm rounded-md bg-[var(--color-success)] text-white hover:bg-green-700 transition-colors"
                  >
                    {t('review.done')}
                  </button>
                  <button onClick={handleConfirm} className="flex items-center gap-2 px-4 py-2 text-sm rounded-md bg-[var(--color-accent)] text-white hover:bg-blue-700 transition-colors">
                    {t('common.confirm')}
                    <ChevronRight size={14} />
                  </button>
                </div>
              )}

              {showResult && currentIndex >= items.length - 1 && (
                <button
                  onClick={handleDoneThenNext}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2.5 text-sm rounded-md bg-[var(--color-accent)] text-white hover:bg-blue-700 transition-colors"
                >
                  <GraduationCap size={16} />
                  {t('review.done')}
                </button>
              )}
            </div>
          )}
        </div>
      )}

    </div>
  )
}
