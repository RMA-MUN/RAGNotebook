import { useEffect, useRef, useState } from 'react'

interface AuthImageProps {
  src: string
  alt: string
  className?: string
}

export default function AuthImage({ src, alt, className }: AuthImageProps) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const mountedRef = useRef(true)

  // src 变化时在 render 阶段同步重置（与原 effect 语义一致，避免 effect 内同步 setState）
  const [prevSrc, setPrevSrc] = useState<string | undefined>(undefined)
  if (src !== prevSrc) {
    setPrevSrc(src)
    setBlobUrl(null)
    setLoaded(false)
  }

  useEffect(() => {
    mountedRef.current = true

    const token = localStorage.getItem('jwt_token')
    const load: Promise<string> = token
      ? fetch(src, { headers: { Authorization: `Bearer ${token}` } })
        .then((res) => {
          if (!res.ok) throw new Error('Auth image load failed')
          return res.blob()
        })
        .then((blob) => URL.createObjectURL(blob))
        .catch(() => src)
      : Promise.resolve(src)

    load.then((url) => {
      if (mountedRef.current) {
        setBlobUrl(url)
      }
    })

    return () => {
      mountedRef.current = false
    }
  }, [src])

  if (!blobUrl) return null

  return (
    <img
      src={blobUrl}
      alt={alt}
      className={className}
      style={loaded ? {} : { opacity: 0 }}
      onLoad={() => setLoaded(true)}
    />
  )
}
