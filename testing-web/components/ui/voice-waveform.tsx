'use client'

import { motion } from 'framer-motion'
import { cn } from '@/lib/utils'

interface VoiceWaveformProps {
  bars?: number
  className?: string
}

export function VoiceWaveform({ bars = 14, className }: VoiceWaveformProps) {
  return (
    <div
      className={cn('flex items-end gap-[3px] h-8', className)}
      aria-label="voice activity"
    >
      {Array.from({ length: bars }).map((_, i) => {
        const min = 4 + (i % 3) * 2
        const max = 18 + ((i * 7) % 14)
        return (
          <motion.span
            key={i}
            className="w-[3px] rounded-full bg-gradient-to-t from-[hsl(224,82%,58%)] to-[hsl(224,90%,75%)]"
            style={{ height: min }}
            animate={{ height: [min, max, min] }}
            transition={{
              duration: 0.9 + (i % 5) * 0.12,
              repeat: Infinity,
              ease: 'easeInOut',
              delay: (i % 7) * 0.06,
            }}
          />
        )
      })}
    </div>
  )
}

export function LiveDot({ className }: { className?: string }) {
  return (
    <span
      className={cn('relative inline-flex h-2.5 w-2.5', className)}
      aria-hidden
    >
      <motion.span
        className="absolute inline-flex h-full w-full rounded-full bg-emerald-400"
        animate={{ scale: [1, 2.2, 1], opacity: [0.6, 0, 0.6] }}
        transition={{ duration: 1.8, repeat: Infinity, ease: 'easeOut' }}
      />
      <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-400 shadow-[0_0_10px_#34d399]" />
    </span>
  )
}
