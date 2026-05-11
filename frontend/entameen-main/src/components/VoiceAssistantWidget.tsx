import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AlertCircle, Loader2, PhoneCall, PhoneOff, X } from "lucide-react";
import { Room, RoomEvent, Track, type RemoteTrack } from "livekit-client";
import { useTranslation } from "react-i18next";

import logo from "@/assets/logo.png";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { createLiveKitDemoSession } from "@/services/api";

type DemoState = "idle" | "creating" | "connecting" | "connected" | "ended" | "error";

const DEFAULT_RESTAURANT_ID = "demo-restaurant";
const WAVE_BARS = [0.26, 0.42, 0.6, 0.82, 1, 0.82, 0.6, 0.42, 0.26];

const VoiceAssistantWidget = () => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [demoState, setDemoState] = useState<DemoState>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isSpeaking, setIsSpeaking] = useState(false);

  const roomRef = useRef<Room | null>(null);
  const mountedRef = useRef(true);
  const audioContainerRef = useRef<HTMLDivElement | null>(null);

  // External pages (e.g. Hero "try voice" CTA) can dispatch window events
  // to open the widget programmatically.
  useEffect(() => {
    const handler = () => setOpen(true);
    window.addEventListener("aloegy:open-voice", handler);
    return () => window.removeEventListener("aloegy:open-voice", handler);
  }, []);

  const removeAttachedAudio = () => {
    const container = audioContainerRef.current;
    if (!container) {
      return;
    }
    while (container.firstChild) {
      container.removeChild(container.firstChild);
    }
  };

  const resetVisualState = (nextState: DemoState) => {
    if (!mountedRef.current) {
      return;
    }
    setDemoState(nextState);
    setIsSpeaking(false);
  };

  const disconnectRoom = async (nextState: DemoState = "ended") => {
    const room = roomRef.current;
    roomRef.current = null;

    if (room) {
      try {
        await room.disconnect(true);
      } catch (error) {
        console.error("Room disconnect error:", error);
      }
    }

    removeAttachedAudio();
    resetVisualState(nextState);
  };

  useEffect(() => {
    return () => {
      mountedRef.current = false;
      const room = roomRef.current;
      roomRef.current = null;
      if (room) {
        void room.disconnect(true);
      }
      removeAttachedAudio();
    };
  }, []);

  const attachRemoteAudio = (track: RemoteTrack) => {
    if (track.kind !== Track.Kind.Audio) {
      return;
    }

    const element = track.attach();
    element.autoplay = true;
    element.playsInline = true;
    element.className = "hidden";
    audioContainerRef.current?.appendChild(element);
  };

  const startLiveDemo = async () => {
    if (demoState === "creating" || demoState === "connecting" || roomRef.current) {
      return;
    }

    setErrorMessage(null);
    setIsSpeaking(false);
    setDemoState("creating");

    try {
      const session = await createLiveKitDemoSession({
        restaurantId: DEFAULT_RESTAURANT_ID,
        participantName: t("widget.participantName"),
      });

      if (!mountedRef.current) {
        return;
      }

      setDemoState("connecting");

      const room = new Room({
        adaptiveStream: true,
        dynacast: false,
        stopLocalTrackOnUnpublish: true,
      });
      roomRef.current = room;

      room
        .on(RoomEvent.Connected, () => {
          if (!mountedRef.current || roomRef.current !== room) {
            return;
          }
          setDemoState("connected");
        })
        .on(RoomEvent.Reconnecting, () => {
          if (!mountedRef.current || roomRef.current !== room) {
            return;
          }
          setDemoState("connecting");
          setIsSpeaking(false);
        })
        .on(RoomEvent.Reconnected, () => {
          if (!mountedRef.current || roomRef.current !== room) {
            return;
          }
          setDemoState("connected");
        })
        .on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
          if (!mountedRef.current || roomRef.current !== room) {
            return;
          }
          setIsSpeaking(speakers.length > 0);
        })
        .on(RoomEvent.TrackSubscribed, (track) => {
          if (!mountedRef.current || roomRef.current !== room) {
            return;
          }
          attachRemoteAudio(track);
        })
        .on(RoomEvent.TrackUnsubscribed, (track) => {
          track.detach().forEach((element) => element.remove());
        })
        .on(RoomEvent.Disconnected, () => {
          if (!mountedRef.current || roomRef.current !== room) {
            return;
          }
          roomRef.current = null;
          removeAttachedAudio();
          resetVisualState("ended");
        });

      void room.prepareConnection(session.livekitUrl, session.token);
      await room.connect(session.livekitUrl, session.token);
      await room.startAudio();
      await room.localParticipant.setMicrophoneEnabled(true);
    } catch (error) {
      console.error("Live demo start error:", error);
      await disconnectRoom("error");
      setErrorMessage(t("widget.error"));
    }
  };

  const handleClose = async () => {
    await disconnectRoom("ended");
    if (mountedRef.current) {
      setOpen(false);
    }
  };

  const stateLabel =
    demoState === "creating"
      ? t("widget.state.creating")
      : demoState === "connecting"
        ? t("widget.state.connecting")
        : demoState === "connected"
          ? isSpeaking
            ? t("widget.state.connectedSpeaking")
            : t("widget.state.connectedListening")
          : demoState === "error"
            ? t("widget.state.error")
            : demoState === "ended"
              ? t("widget.state.ended")
              : t("widget.state.idle");

  const hasActiveCall = demoState === "connected" || demoState === "connecting";
  const buttonBusy = demoState === "creating" || demoState === "connecting";
  const waveMode =
    demoState === "error"
      ? "error"
      : demoState === "ended"
        ? "ended"
        : demoState === "connected"
          ? isSpeaking
            ? "speaking"
            : "live"
          : demoState === "creating" || demoState === "connecting"
            ? "connecting"
            : "idle";

  return (
    <>
      <AnimatePresence>
        {!open && (
          <motion.button
            initial={{ scale: 0, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0, opacity: 0 }}
            transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
            onClick={() => setOpen(true)}
            className="group fixed bottom-6 start-6 z-50 flex items-center gap-2.5 rounded-full bg-primary px-4 py-3 glow-brand transition-all duration-250 ease-smooth hover:scale-[1.04] hover:bg-primary/90"
          >
            <img src={logo} alt={t("widget.title")} className="h-7 w-7 object-contain" />
            <span className="text-sm font-medium text-primary-foreground">
              {t("widget.label")}
            </span>
          </motion.button>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: 20, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 20, scale: 0.95 }}
            transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            className="fixed bottom-6 start-6 z-50 w-[360px] max-w-[calc(100vw-3rem)] overflow-hidden rounded-3xl border border-border/60 bg-card ring-lift glow-brand"
          >
            <div className="flex items-center justify-between px-5 py-4">
              <div className="flex items-center gap-2.5">
                <img src={logo} alt={t("widget.title")} className="h-7 w-7 object-contain" />
                <div>
                  <p className="text-sm font-semibold tracking-tight text-foreground">
                    {t("widget.title")}
                  </p>
                  <p className="text-xs text-muted-foreground">{stateLabel}</p>
                </div>
              </div>
              <button
                onClick={() => {
                  void handleClose();
                }}
                className="text-muted-foreground transition-colors duration-250 hover:text-foreground"
                aria-label="Close"
              >
                <X size={18} />
              </button>
            </div>

            <div className="px-4 pb-4">
              <div className="relative overflow-hidden rounded-2xl border border-border/60 bg-background/50 px-4 py-8 ring-lift">
                <div className="absolute inset-x-0 top-1/2 h-24 -translate-y-1/2 bg-primary/10 blur-3xl" />

                <div className="relative mx-auto flex h-28 max-w-[220px] items-end justify-center gap-2">
                  {WAVE_BARS.map((heightFactor, index) => (
                    <motion.div
                      key={index}
                      className={cn(
                        "w-2 rounded-full",
                        waveMode === "error"
                          ? "bg-destructive/70"
                          : waveMode === "ended"
                            ? "bg-muted-foreground/35"
                            : waveMode === "idle"
                              ? "bg-primary/35"
                              : waveMode === "connecting"
                                ? "bg-primary/60"
                                : waveMode === "live"
                                  ? "bg-primary/70"
                                  : "bg-primary",
                      )}
                      style={{ height: 72 }}
                      animate={{
                        scaleY:
                          waveMode === "error"
                            ? [0.16, 0.22, 0.16]
                            : waveMode === "ended"
                              ? [0.12, 0.18, 0.12]
                              : waveMode === "idle"
                                ? [0.2 * heightFactor, 0.34 * heightFactor + 0.12, 0.2 * heightFactor]
                                : waveMode === "connecting"
                                  ? [0.28 * heightFactor, 0.62 * heightFactor + 0.18, 0.28 * heightFactor]
                                  : waveMode === "live"
                                    ? [0.34 * heightFactor, 0.82 * heightFactor + 0.2, 0.34 * heightFactor]
                                    : [0.5 * heightFactor, 1.2 * heightFactor + 0.22, 0.5 * heightFactor],
                      }}
                      transition={{
                        duration: 1 + index * 0.08,
                        repeat: Number.POSITIVE_INFINITY,
                        ease: "easeInOut",
                        delay: index * 0.05,
                      }}
                    />
                  ))}
                </div>

                <div className="relative mt-4 text-center">
                  <p className="text-sm font-medium text-foreground">{stateLabel}</p>
                  {errorMessage ? (
                    <p className="mt-2 inline-flex items-center gap-1 text-xs text-destructive">
                      <AlertCircle size={12} />
                      {errorMessage}
                    </p>
                  ) : (
                    <p className="mt-2 text-xs text-muted-foreground">
                      {demoState === "connected"
                        ? t("widget.helper.connected")
                        : t("widget.helper.idle")}
                    </p>
                  )}
                </div>
              </div>

              <div className="mt-3">
                {!hasActiveCall ? (
                  <Button
                    onClick={() => {
                      void startLiveDemo();
                    }}
                    className="h-11 w-full gap-2 rounded-full bg-primary text-sm font-medium text-primary-foreground transition-all duration-250 ease-smooth hover:bg-primary/90"
                    disabled={buttonBusy}
                  >
                    {buttonBusy ? <Loader2 size={16} className="animate-spin" /> : <PhoneCall size={16} />}
                    {demoState === "error" || demoState === "ended"
                      ? t("widget.button.startAgain")
                      : t("widget.button.start")}
                  </Button>
                ) : (
                  <Button
                    onClick={() => {
                      void disconnectRoom("ended");
                    }}
                    variant="destructive"
                    className="h-11 w-full gap-2 rounded-full"
                  >
                    <PhoneOff size={16} />
                    {t("widget.button.end")}
                  </Button>
                )}
              </div>
            </div>

            <div ref={audioContainerRef} className="hidden" aria-hidden="true" />
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
};

export default VoiceAssistantWidget;
