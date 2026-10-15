import { AuthedImage, screenshotPath } from "./AuthedImage"

export function ScreenshotPanel({ stateId }: { stateId: string | null }) {
  return (
    <div className="space-y-1">
      <h3 className="text-sm font-medium">Latest screen</h3>
      {stateId ? (
        <AuthedImage
          path={screenshotPath(stateId)}
          alt="Latest screenshot captured by the crawler"
          className="w-full rounded-md border"
        />
      ) : (
        <div className="flex h-40 items-center justify-center rounded-md border text-sm text-muted-foreground">
          No screenshot yet
        </div>
      )}
    </div>
  )
}
