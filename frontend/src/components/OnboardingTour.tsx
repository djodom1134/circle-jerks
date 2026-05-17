import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, X } from "lucide-react";

const ONBOARDING_KEY = "circlejerks_onboarding_seen_v1";

interface Step {
  title: string;
  body: string;
  hint?: string;
}

const STEPS: Step[] = [
  {
    title: "Tell us where you live",
    body:
      "Hit the locate-fixed icon to use your browser's location, or type an address in the top-right “User location” field. The green “Home” dot and the dashed circle around it are how we know who's overhead vs. just nearby.",
    hint: "Top-right: USER LOCATION field",
  },
  {
    title: "Pick a time window",
    body:
      "5 min, 30 min, 1 hour, 6 hours, or Today. The site streams live ADS-B every 10 seconds, so wider windows take a few seconds longer to build up. Start with 1 hour while the buffer fills.",
    hint: "Top-left of the toolbar",
  },
  {
    title: "Look at the offender list",
    body:
      "The right-hand side bar shows every aircraft we've watched circling, doing touch-and-gos, or passing overhead, ranked worst-first. Each one shows its origin airport and how many times you've already reported it.",
    hint: "Right side: Worst Offenders",
  },
  {
    title: "Scroll down to the report panel",
    body:
      "Hit “Prepare complaint” or scroll until you see the sliders. Pick “Selected offender” for one plane, or “All offenders” for a single combined complaint covering everything in this window.",
    hint: "Below the map",
  },
  {
    title: "Tune what goes in the message",
    body:
      "Tone sliders shape the voice (angry / formal / neighborly). “Message includes” toggles which facts to add: circle count, altitude over your house, dB at home, etc. We don't fabricate data — every fact comes from the ADS-B feed.",
    hint: "Sliders + Message includes",
  },
  {
    title: "Click Copy",
    body:
      "Hits your clipboard. The aircraft also get logged to our shared database so other neighbors can see which planes are the worst repeat offenders.",
    hint: "Copy button next to “Ready to submit complaint”",
  },
  {
    title: "Paste it where it counts",
    body:
      "We provide links to your local airport's noise complaint form and the FAA's ANCIR portal. Pasting is the only step we can't automate. The more residents who report, the more pressure on local airports to enforce noise abatement.",
    hint: "“Open complaint form” and “File with FAA” buttons",
  },
  {
    title: "If this helps you, help it stay free",
    body:
      "We run this on weekends and spare evenings. Server bills, ADS-B Exchange and FlightAware feeds, AI generation — all paid out of pocket. Anything you drop in the Buy Me a Coffee tip jar goes 100% to keeping this running and pushing for quieter skies.",
    hint: "Thanks-to-our-supporters at the bottom of the page",
  },
];

export function shouldShowOnboarding(): boolean {
  try {
    return window.localStorage.getItem(ONBOARDING_KEY) !== "true";
  } catch {
    return true;
  }
}

export default function OnboardingTour({ onClose }: { onClose: () => void }) {
  const [stepIdx, setStepIdx] = useState(0);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") finish();
      if (event.key === "ArrowRight") next();
      if (event.key === "ArrowLeft") prev();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stepIdx]);

  function next() {
    if (stepIdx < STEPS.length - 1) setStepIdx(stepIdx + 1);
    else finish();
  }
  function prev() {
    if (stepIdx > 0) setStepIdx(stepIdx - 1);
  }
  function finish() {
    try {
      window.localStorage.setItem(ONBOARDING_KEY, "true");
    } catch {
      // no-op
    }
    onClose();
  }

  const step = STEPS[stepIdx];
  const progress = ((stepIdx + 1) / STEPS.length) * 100;

  return (
    <div className="onboarding-backdrop" role="dialog" aria-modal="true" aria-labelledby="onboarding-title">
      <div className="onboarding-modal">
        <header>
          <span className="onboarding-step-count">
            Step {stepIdx + 1} of {STEPS.length}
          </span>
          <button type="button" className="onboarding-close" onClick={finish} aria-label="Close onboarding">
            <X size={18} aria-hidden="true" />
          </button>
        </header>
        <div className="onboarding-progress">
          <div className="onboarding-progress-bar" style={{ width: `${progress}%` }} />
        </div>
        <h2 id="onboarding-title">{step.title}</h2>
        <p>{step.body}</p>
        {step.hint && <div className="onboarding-hint">↳ {step.hint}</div>}
        <footer>
          <button type="button" className="onboarding-skip" onClick={finish}>
            Skip tour
          </button>
          <div className="onboarding-nav">
            <button
              type="button"
              className="onboarding-button ghost"
              onClick={prev}
              disabled={stepIdx === 0}
              aria-label="Previous step"
            >
              <ChevronLeft size={16} aria-hidden="true" />
              Back
            </button>
            <button type="button" className="onboarding-button primary" onClick={next}>
              {stepIdx < STEPS.length - 1 ? "Next" : "Got it"}
              {stepIdx < STEPS.length - 1 && <ChevronRight size={16} aria-hidden="true" />}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
