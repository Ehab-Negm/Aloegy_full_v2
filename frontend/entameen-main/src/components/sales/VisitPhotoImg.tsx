import { useEffect, useState } from "react";
import { ImageIcon } from "lucide-react";

import { fetchVisitPhotoBlobUrl } from "@/services/api";

interface VisitPhotoImgProps {
  url: string;
  alt?: string;
  className?: string;
}

/**
 * Visit photos sit behind an auth-required endpoint, so a plain
 * <img src="/api/sales/visit-photos/X"> won't render. This component
 * fetches the image with the auth header, converts it to a blob URL,
 * and revokes that URL on unmount to avoid leaking memory.
 */
const VisitPhotoImg = ({ url, alt = "", className }: VisitPhotoImgProps) => {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let revoked = false;
    let createdBlobUrl: string | null = null;
    setError(false);
    setBlobUrl(null);
    void (async () => {
      try {
        const result = await fetchVisitPhotoBlobUrl(url);
        if (revoked) {
          URL.revokeObjectURL(result);
          return;
        }
        createdBlobUrl = result;
        setBlobUrl(result);
      } catch (err) {
        console.warn("Failed to load visit photo", err);
        if (!revoked) setError(true);
      }
    })();
    return () => {
      revoked = true;
      if (createdBlobUrl) URL.revokeObjectURL(createdBlobUrl);
    };
  }, [url]);

  if (error) {
    return (
      <div
        className={`flex aspect-square items-center justify-center rounded-lg bg-muted text-muted-foreground ${className || ""}`}
      >
        <ImageIcon size={16} />
      </div>
    );
  }

  if (!blobUrl) {
    return (
      <div className={`aspect-square animate-pulse rounded-lg bg-muted ${className || ""}`} />
    );
  }

  return (
    <a
      href={blobUrl}
      target="_blank"
      rel="noreferrer"
      className={`block aspect-square overflow-hidden rounded-lg border border-border/50 ${className || ""}`}
    >
      <img src={blobUrl} alt={alt} className="h-full w-full object-cover" />
    </a>
  );
};

export default VisitPhotoImg;
