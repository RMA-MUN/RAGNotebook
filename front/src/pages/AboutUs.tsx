import { useTranslation } from 'react-i18next'
import { GitFork } from 'lucide-react'

export default function AboutUs() {
  const { t } = useTranslation()

  return (
    <div className="max-w-2xl mx-auto py-8 px-6">
      <h1 className="font-heading text-xl font-semibold text-[var(--color-text)] mb-6">{t('about.title')}</h1>

      <div className="bg-[var(--color-card)] rounded-lg border border-[var(--color-border)] p-6 space-y-6">
        <p className="text-sm text-[var(--color-text-secondary)] leading-relaxed">
          {t('about.description')}
        </p>

        <div>
          <h3 className="text-sm font-medium text-[var(--color-text)] mb-3">{t('about.techStack')}</h3>
          <div className="flex flex-wrap gap-2">
            {['React', 'FastAPI', 'LangChain', 'MySQL', 'Redis', 'ChromaDB'].map((tech) => (
              <span key={tech} className="px-2.5 py-1 text-xs rounded-full bg-[var(--color-accent-bg)] text-[var(--color-accent)]">
                {tech}
              </span>
            ))}
          </div>
        </div>

        <div>
          <h3 className="text-sm font-medium text-[var(--color-text)] mb-3">{t('about.features')}</h3>
          <ul className="space-y-2">
            {['aiChat', 'noteTaking', 'knowledgeBase', 'review'].map((key) => (
              <li key={key} className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
                <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-accent)]" />
                {t(`about.featureList.${key}`)}
              </li>
            ))}
          </ul>
        </div>

        <a
          href="https://github.com/RMA-MUN/LangChain-RAG-FastAPI-Service"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 text-sm text-[var(--color-accent)] hover:underline"
        >
          <GitFork size={16} />
          {t('about.github')}
        </a>
      </div>
    </div>
  )
}
