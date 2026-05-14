'use client'

import Image from 'next/image'
import { motion } from 'framer-motion'
import { cn } from '@/lib/utils'

interface CharacterSceneProps {
  src?: string
  alt?: string
  className?: string
}

const TALK_OPACITY = [0.15, 0.85, 0.35, 0.95, 0.25, 0.7, 0.15, 0.9, 0.4, 0.15]
const TALK_SCALE = [0.85, 1.15, 0.95, 1.2, 0.9, 1.1, 0.85, 1.18, 1.0, 0.85]
const HEAD_TILT = [0, -0.8, 0.4, -0.6, 0.5, -0.3, 0.6, -0.5, 0.3, 0]
const HEAD_Y = [0, -1.2, 0.6, -0.9, 0.4, -0.7, 0.9, -0.6, 0.3, 0]

export function CharacterScene({
  src = '/character.png',
  alt = 'AloEgy character',
  className,
}: CharacterSceneProps) {
  return (
    <div className={cn('relative w-full h-full', className)}>
      {/* Soft halo behind the character */}
      <motion.div
        aria-hidden
        className="absolute inset-0 flex items-center justify-center"
      >
        <motion.div
          className="h-[62%] w-[62%] rounded-full"
          style={{
            background:
              'radial-gradient(closest-side, hsla(224,90%,70%,0.45), hsla(224,82%,51%,0.12) 55%, transparent 75%)',
            filter: 'blur(24px)',
          }}
          animate={{ scale: [0.96, 1.04, 0.96], opacity: [0.55, 0.85, 0.55] }}
          transition={{ duration: 3.6, repeat: Infinity, ease: 'easeInOut' }}
        />
      </motion.div>

      {/* Character + talking micro-motion */}
      <motion.div
        initial={{ opacity: 0, scale: 0.92, y: 24 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ duration: 0.8, ease: [0.22, 1, 0.36, 1] }}
        className="absolute inset-0 flex items-end justify-center"
      >
        {/* Slow idle bob */}
        <motion.div
          animate={{ y: [0, -6, 0] }}
          transition={{ duration: 5, repeat: Infinity, ease: 'easeInOut' }}
          className="relative h-[92%] w-full"
        >
          {/* Talking head-bob layer (fast, irregular) */}
          <motion.div
            animate={{ rotate: HEAD_TILT, y: HEAD_Y }}
            transition={{
              duration: 2.4,
              repeat: Infinity,
              ease: 'easeInOut',
              times: [0, 0.12, 0.22, 0.34, 0.46, 0.58, 0.7, 0.82, 0.92, 1],
            }}
            style={{ transformOrigin: '50% 85%' }}
            className="relative h-full w-full"
          >
            <Image
              src={src}
              alt={alt}
              fill
              priority
              sizes="(max-width: 768px) 80vw, 40vw"
              className="object-contain drop-shadow-[0_30px_50px_rgba(29,78,216,0.45)]"
            />

            {/* Mouthpiece glow — pulses on speech rhythm.
                The headset mouthpiece sits roughly at ~38% from left, ~28% from top of the image. */}
            <motion.div
              aria-hidden
              className="absolute pointer-events-none mix-blend-screen"
              style={{
                left: '32%',
                top: '24%',
                width: '14%',
                height: '12%',
                background:
                  'radial-gradient(closest-side, hsl(195 100% 70% / 0.95), hsl(195 100% 60% / 0.5) 40%, transparent 70%)',
                filter: 'blur(6px)',
                borderRadius: '50%',
              }}
              animate={{ opacity: TALK_OPACITY, scale: TALK_SCALE }}
              transition={{
                duration: 2.4,
                repeat: Infinity,
                ease: 'easeInOut',
                times: [0, 0.12, 0.22, 0.34, 0.46, 0.58, 0.7, 0.82, 0.92, 1],
              }}
            />

            {/* Ear / headset speaker glow — slower, receiving audio.
                Sits on the upper right of the head, ~62% left, ~14% top. */}
            <motion.div
              aria-hidden
              className="absolute pointer-events-none mix-blend-screen"
              style={{
                left: '58%',
                top: '11%',
                width: '12%',
                height: '10%',
                background:
                  'radial-gradient(closest-side, hsl(160 100% 65% / 0.9), hsl(160 100% 55% / 0.45) 45%, transparent 75%)',
                filter: 'blur(5px)',
                borderRadius: '50%',
              }}
              animate={{ opacity: [0.2, 0.85, 0.4, 0.7, 0.2] }}
              transition={{
                duration: 3.2,
                repeat: Infinity,
                ease: 'easeInOut',
              }}
            />

            {/* Eye / visor flicker — faint cyan shimmer over the face. */}
            <motion.div
              aria-hidden
              className="absolute pointer-events-none mix-blend-screen"
              style={{
                left: '36%',
                top: '14%',
                width: '28%',
                height: '8%',
                background:
                  'linear-gradient(90deg, transparent, hsl(195 100% 75% / 0.55), transparent)',
                filter: 'blur(3px)',
                borderRadius: '40%',
              }}
              animate={{ opacity: [0.0, 0.6, 0.0, 0.8, 0.0] }}
              transition={{
                duration: 4.5,
                repeat: Infinity,
                ease: 'easeInOut',
              }}
            />
          </motion.div>
        </motion.div>
      </motion.div>

      {/* Speech bubble */}
      <motion.div
        initial={{ opacity: 0, y: 10, scale: 0.9 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ delay: 1.2, duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
        className="absolute top-6 left-4 md:left-10 z-10"
      >
        <div className="relative rounded-2xl rounded-bl-sm bg-white/10 backdrop-blur border border-white/15 px-3.5 py-2 text-xs md:text-sm text-neutral-100 shadow-lg shadow-black/20">
          <span dir="rtl">أهلاً! اطلب اللي نفسك فيه</span>
          <span className="absolute -bottom-1 left-2 h-2 w-2 rotate-45 bg-white/10 border-l border-b border-white/15" />
        </div>
      </motion.div>

      {/* Ground shadow */}
      <motion.div
        aria-hidden
        className="absolute bottom-4 left-1/2 -translate-x-1/2 h-3 w-44 rounded-full bg-[hsl(224,82%,30%)]/60 blur-xl"
        animate={{ scaleX: [1, 0.88, 1], opacity: [0.55, 0.85, 0.55] }}
        transition={{ duration: 5, repeat: Infinity, ease: 'easeInOut' }}
      />
    </div>
  )
}
