'use client'

import { CharacterScene } from '@/components/ui/character-scene'
import { Card } from '@/components/ui/card'
import { Spotlight } from '@/components/ui/spotlight'
import { VoiceWaveform, LiveDot } from '@/components/ui/voice-waveform'
import { motion, type Variants } from 'framer-motion'
import { Phone, Sparkles, Zap, Clock, PhoneCall } from 'lucide-react'

const EASE: [number, number, number, number] = [0.22, 1, 0.36, 1]

const stagger: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.12, delayChildren: 0.15 } },
}
const fadeUp: Variants = {
  hidden: { opacity: 0, y: 24 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.7, ease: EASE },
  },
}

const TICKER = [
  { icon: Zap, label: 'مبيعات +٢٠٪' },
  { icon: Clock, label: 'شغال ٢٤ ساعة' },
  { icon: PhoneCall, label: 'مفيش خط مشغول' },
  { icon: Sparkles, label: 'لهجة مصرية صميم' },
  { icon: Zap, label: 'رد في أقل من ثانية' },
  { icon: Clock, label: 'مفيش إجازات' },
]

export function AloEgyHero() {
  return (
    <Card className="w-full min-h-[640px] bg-[hsl(225,30%,6%)] border-white/5 relative overflow-hidden">
      {/* Animated gradient blobs */}
      <motion.div
        aria-hidden
        className="absolute -top-32 -left-32 h-[420px] w-[420px] rounded-full bg-[radial-gradient(closest-side,hsla(224,82%,55%,0.55),transparent_70%)] blur-3xl"
        animate={{ x: [0, 60, -20, 0], y: [0, 40, -30, 0], scale: [1, 1.1, 0.95, 1] }}
        transition={{ duration: 14, repeat: Infinity, ease: 'easeInOut' }}
      />
      <motion.div
        aria-hidden
        className="absolute -bottom-40 -right-32 h-[460px] w-[460px] rounded-full bg-[radial-gradient(closest-side,hsla(228,80%,40%,0.55),transparent_70%)] blur-3xl"
        animate={{ x: [0, -50, 30, 0], y: [0, -30, 40, 0], scale: [1, 1.08, 0.92, 1] }}
        transition={{ duration: 16, repeat: Infinity, ease: 'easeInOut' }}
      />

      {/* Animated dotted grid */}
      <div
        aria-hidden
        className="absolute inset-0 opacity-[0.18] [background-image:radial-gradient(hsla(224,90%,70%,0.45)_1px,transparent_1px)] [background-size:22px_22px] [mask-image:radial-gradient(ellipse_at_center,black,transparent_70%)]"
      />

      <Spotlight
        className="-top-40 right-0 md:right-60 md:-top-20"
        fill="hsl(224,90%,70%)"
      />

      <div className="flex h-full flex-col md:flex-row-reverse relative z-10">
        {/* Right: text (RTL) */}
        <motion.div
          variants={stagger}
          initial="hidden"
          animate="show"
          className="flex-1 p-8 md:p-12 flex flex-col justify-center text-right"
        >
          <motion.div variants={fadeUp} className="self-end flex items-center gap-3">
            <span className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-neutral-300 backdrop-blur">
              <LiveDot />
              <span>على الهوا</span>
            </span>
            <span className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-neutral-300 backdrop-blur">
              <Sparkles className="h-3.5 w-3.5 text-[hsl(224,90%,70%)]" />
              <span>الذكاء الاصطناعي المتربي في مصر</span>
            </span>
          </motion.div>

          <motion.h1
            variants={fadeUp}
            className="mt-6 text-5xl md:text-7xl font-extrabold leading-[1.05] relative"
          >
            <span className="relative inline-block bg-clip-text text-transparent bg-[linear-gradient(110deg,#ffffff,40%,#9ec6ff,60%,#ffffff)] bg-[length:200%_100%]">
              <motion.span
                className="block"
                animate={{ backgroundPositionX: ['200%', '0%'] }}
                transition={{ duration: 6, repeat: Infinity, ease: 'linear' }}
                style={{
                  backgroundImage:
                    'linear-gradient(110deg,#ffffff,40%,#9ec6ff,60%,#ffffff)',
                  backgroundSize: '200% 100%',
                  WebkitBackgroundClip: 'text',
                  WebkitTextFillColor: 'transparent',
                }}
              >
                ألو إيچي
              </motion.span>
            </span>
            <motion.span
              variants={fadeUp}
              className="block text-2xl md:text-3xl mt-3 font-semibold text-[hsl(224,90%,72%)]"
            >
              أسرع ألو في مصر
            </motion.span>
          </motion.h1>

          <motion.p
            variants={fadeUp}
            className="mt-5 text-neutral-300 max-w-lg self-end leading-relaxed text-base md:text-lg"
          >
            وكيل صوتي ابن بلد بيتكلم مصري زيك بالظبط، بيأخد أوردرات المطاعم
            بدل الموظف، وبيزوّد مبيعاتك ٢٠٪ من الإضافات الذكية. مفيش تليفون
            هيرن ومحدش يرد عليه تاني.
          </motion.p>

          {/* Voice activity strip */}
          <motion.div
            variants={fadeUp}
            className="mt-6 self-end flex items-center gap-3 rounded-2xl border border-white/10 bg-white/5 px-4 py-2.5 backdrop-blur"
          >
            <span className="text-xs text-neutral-400">بيتكلم دلوقتي</span>
            <VoiceWaveform />
            <span className="text-xs text-[hsl(224,90%,75%)] font-semibold">
              ٠٠:٠٧
            </span>
          </motion.div>

          <motion.div variants={fadeUp} className="mt-8 flex gap-3 self-end">
            <motion.button
              whileHover={{ scale: 1.04, y: -2 }}
              whileTap={{ scale: 0.97 }}
              className="group inline-flex items-center gap-2 rounded-xl bg-[hsl(224,82%,58%)] px-6 py-3 text-sm font-bold text-white shadow-[0_10px_40px_-10px_hsl(224,82%,58%)] transition hover:bg-[hsl(224,82%,52%)] relative overflow-hidden"
              type="button"
            >
              <motion.span
                aria-hidden
                className="absolute inset-0 bg-gradient-to-r from-transparent via-white/30 to-transparent"
                animate={{ x: ['-100%', '200%'] }}
                transition={{ duration: 2.6, repeat: Infinity, ease: 'easeInOut' }}
              />
              <motion.span
                animate={{ rotate: [0, -15, 15, -10, 10, 0] }}
                transition={{
                  duration: 1.2,
                  repeat: Infinity,
                  repeatDelay: 1.6,
                  ease: 'easeInOut',
                }}
                className="relative"
              >
                <Phone className="h-4 w-4" />
              </motion.span>
              <span className="relative">جرب صوت ألو إيچي</span>
            </motion.button>
            <motion.button
              whileHover={{ scale: 1.04, y: -2 }}
              whileTap={{ scale: 0.97 }}
              className="inline-flex items-center rounded-xl border border-white/15 bg-white/5 px-6 py-3 text-sm font-bold text-neutral-200 transition hover:bg-white/10"
              type="button"
            >
              اعرف أكتر
            </motion.button>
          </motion.div>
        </motion.div>

        {/* Left: character */}
        <div className="flex-1 relative min-h-[380px] md:min-h-0">
          <CharacterScene
            src="/character.png"
            alt="شخصية ألو إيچي"
            className="w-full h-full"
          />
        </div>
      </div>

      {/* Marquee ticker at the bottom */}
      <div className="absolute inset-x-0 bottom-0 z-10 border-t border-white/5 bg-black/40 backdrop-blur">
        <div className="overflow-hidden">
          <motion.div
            className="flex gap-10 whitespace-nowrap py-3 px-6 text-sm text-neutral-300"
            animate={{ x: ['0%', '-50%'] }}
            transition={{ duration: 28, repeat: Infinity, ease: 'linear' }}
          >
            {[...TICKER, ...TICKER, ...TICKER].map((t, i) => {
              const Icon = t.icon
              return (
                <span key={i} className="inline-flex items-center gap-2">
                  <Icon className="h-4 w-4 text-[hsl(224,90%,72%)]" />
                  <span>{t.label}</span>
                  <span className="text-white/20">•</span>
                </span>
              )
            })}
          </motion.div>
        </div>
      </div>
    </Card>
  )
}
