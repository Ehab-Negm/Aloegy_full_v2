import { motion } from "framer-motion";

interface MeshBackgroundProps {
  /** Show the dot-grid layer. */
  dotGrid?: boolean;
  /** Show animated gradient blobs. */
  blobs?: boolean;
  /** Tone down the dot-grid via a radial mask that fades at edges. */
  fadeMask?: boolean;
  className?: string;
}

/**
 * Subtle, layered background used on hero and section transitions:
 *  - optional radial-fading dot grid
 *  - optional slow-drifting brand-tinted blobs
 *
 * Pure decoration — pointer-events disabled and absolutely positioned.
 */
const MeshBackground = ({
  dotGrid = true,
  blobs = true,
  fadeMask = true,
  className = "",
}: MeshBackgroundProps) => {
  return (
    <div
      aria-hidden="true"
      className={`pointer-events-none absolute inset-0 overflow-hidden ${className}`}
    >
      {dotGrid && (
        <div
          className={`absolute inset-0 bg-dot-grid ${fadeMask ? "mask-radial-fade" : ""}`}
        />
      )}

      {blobs && (
        <>
          <motion.div
            className="absolute -top-32 left-1/4 h-[420px] w-[420px] rounded-full"
            style={{
              background:
                "radial-gradient(circle at center, hsl(var(--brand) / 0.35) 0%, transparent 70%)",
              filter: "blur(70px)",
            }}
            animate={{
              x: [0, 40, -30, 0],
              y: [0, -30, 20, 0],
              scale: [1, 1.1, 0.95, 1],
            }}
            transition={{
              duration: 22,
              repeat: Infinity,
              ease: [0.45, 0.05, 0.55, 0.95],
            }}
          />
          <motion.div
            className="absolute top-40 right-1/4 h-[360px] w-[360px] rounded-full"
            style={{
              background:
                "radial-gradient(circle at center, hsl(var(--brand-glow) / 0.28) 0%, transparent 70%)",
              filter: "blur(80px)",
            }}
            animate={{
              x: [0, -50, 30, 0],
              y: [0, 30, -20, 0],
              scale: [1, 0.9, 1.15, 1],
            }}
            transition={{
              duration: 26,
              repeat: Infinity,
              ease: [0.45, 0.05, 0.55, 0.95],
              delay: 4,
            }}
          />
        </>
      )}
    </div>
  );
};

export default MeshBackground;
