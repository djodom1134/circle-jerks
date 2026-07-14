import { useMemo } from "react";
import type { LedgerResponse } from "../lib/types";
import { computeRevenue } from "../lib/calculator";
import { formatCurrency } from "../lib/format";

const ILLUSTRATIVE_FEE = 10;

const ON_AIRPORT_PROJECTS = [
  {
    icon: "🛬",
    name: "Taxiway Rehabilitation Reserve",
    cost: 2_500_000,
    desc: "Seal, repair, and preserve hard surfaces before failure turns maintenance into replacement.",
  },
  {
    icon: "⚡",
    name: "Electric Aircraft Charging Apron",
    cost: 850_000,
    desc: "Charging, power management, and showcase infrastructure for quieter next-generation aircraft.",
  },
  {
    icon: "☕",
    name: "Community FBO",
    cost: 1_800_000,
    desc: "A destination terminal with meeting rooms, local food, exhibits, and public viewing terraces — on airport property.",
  },
  {
    icon: "🔇",
    name: "Neighborhood Noise Monitoring Network",
    cost: 280_000,
    desc: "Permanent calibrated monitors and a transparent public dashboard tied to aircraft tracks.",
  },
  {
    icon: "🌱",
    name: "Unleaded Fuel Transition Fund",
    cost: 500_000,
    desc: "Infrastructure and incentives that accelerate the move away from leaded aviation gasoline.",
  },
] as const;

function fundedInLabel(cost: number, annualRevenue: number): string {
  if (annualRevenue <= 0) return "Not calculable at zero observed activity";
  const years = cost / annualRevenue;
  if (years < 1) return `Funded in ${Math.ceil(years * 12)} months`;
  return `Funded in ${years.toFixed(1)} years`;
}

export function LawfulUse({ data }: { data: LedgerResponse }) {
  const illustrativeAnnual = useMemo(
    () =>
      computeRevenue({
        runwayUses: data.summary.runway_uses,
        windowDays: data.window.days,
        feePerUse: ILLUSTRATIVE_FEE,
      }).perYear,
    [data.summary.runway_uses, data.window.days],
  );

  return (
    <section id="lawful-use" className="section-pad">
      <div className="section-heading">
        <p className="kicker">What The Law Allows</p>
        <h2>Turn runway uses into on-airport capital</h2>
        <p>
          Every project below stays on the airport, at a {formatCurrency(ILLUSTRATIVE_FEE)}-per-use illustrative fee
          applied to the runway uses actually observed this window.
        </p>
      </div>

      <div className="project-grid">
        {ON_AIRPORT_PROJECTS.map((project) => (
          <article className="project-card" key={project.name}>
            <div className="project-icon" aria-hidden="true">
              {project.icon}
            </div>
            <h3>{project.name}</h3>
            <div className="project-cost">{formatCurrency(project.cost)}</div>
            <p>{project.desc}</p>
            <span className="fund-time">{fundedInLabel(project.cost, illustrativeAnnual)}</span>
          </article>
        ))}
      </div>

      <p className="legal-note">
        <strong>Airport revenue stays on the airport.</strong> That isn't a footnote limiting this idea; it's the
        reason every project above is actually buildable without a legal fight — nothing here funds anything off
        airport property. See{" "}
        <a href="#obligation">the federal obligation Longmont signed</a> for the exact statute and grant-assurance
        language that locks this revenue to {data.airport_icao} itself.
      </p>
    </section>
  );
}
