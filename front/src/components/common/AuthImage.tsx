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

  useEffect(() => {
    mountedRef.current = true
    setBlobUrl(null)
    setLoaded(false)

    const token = localStorage.getItem('jwt_token')
    if (!token) {
      setBlobUrl(src)
      return
    }

    fetch(src, { headers: { Authorization: `Bearer ${token}` } })
      .then((res) => {
        if (!res.ok) throw new Error('Auth image load failed')
        return res.blob()
      })
      .then((blob) => {
        if (mountedRef.current) {
          setBlobUrl(URL.createObjectURL(blob))
        }
      })
      .catch(() => {
        if (mountedRef.current) {
          setBlobUrl(src)
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
