import { useEffect, useState } from "react"

import { apiBase, authHeaders } from "@/lib/runStream"

const cache = new Map<string, string>()

/** <img> cannot send an Authorization header, so fetch the bytes and use an object URL. */
export function AuthedImage({
  path,
  alt,
  className,
}: {
  path: string | null
  alt: string
  className?: string
}) {
  const [src, setSrc] = useState<string | null>(
    path ? (cache.get(path) ?? null) : null,
  )
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!path) return
    setFailed(false)
    const hit = cache.get(path)
    if (hit) {
      setSrc(hit)
      return
    }
    let cancelled = false
    void (async () => {
      try {
        const r = await fetch(`${apiBase()}${path}`, { headers: authHeaders() })
        if (!r.ok) throw new Error(String(r.status))
        const url = URL.createObjectURL(await r.blob())
        cache.set(path, url)
        if (!cancelled) setSrc(url)
      } catch {
        if (!cancelled) setFailed(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [path])

  if (failed) {
    return (
      <div className="flex h-40 items-center justify-center rounded-md border text-sm text-muted-foreground">
        Screenshot unavailable
      </div>
    )
  }
  if (!src) return <div className="h-40 animate-pulse rounded-md bg-muted" />
  return <img src={src} alt={alt} className={className} />
}

export const screenshotPath = (stateId: string) =>
  `/api/v1/states/${stateId}/screenshot`
