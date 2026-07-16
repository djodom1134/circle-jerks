import { useEffect, useState } from "react";
import { AlertTriangle, ExternalLink, Info, ShieldAlert } from "lucide-react";
import { getAircraftProfile, type AircraftProfile, type OwnerType } from "../lib/api";

const OWNER_LABELS: Record<OwnerType, string> = {
  individual: "Individual",
  llc: "LLC",
  corporation: "Corporation",
  government: "Government",
  flight_school: "Flight school",
  university: "University",
  club: "Club",
  trust: "Trust",
  skydiving: "Skydiving",
  commercial_airline: "Commercial airline",
  unknown: "Unknown",
};

function ownerBadgeClass(owner: OwnerType): string {
  return `owner-badge owner-${owner}`;
}

interface Props {
  icao24?: string | null;
  callsign?: string | null;
}

export function AircraftProfileCard({ icao24, callsign }: Props) {
  const [profile, setProfile] = useState<AircraftProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!icao24 && !callsign) {
      setProfile(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    getAircraftProfile({ icaoHex: icao24, callsign })
      .then((p) => {
        if (!cancelled) setProfile(p);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [icao24, callsign]);

  if (!icao24 && !callsign) return null;

  const a = profile?.aircraft;
  const r = profile?.registration;
  const o = profile?.registrant;
  const ownerType = o?.ownerType ?? "unknown";
  const ownerLabel = OWNER_LABELS[ownerType];
  const yearLine = [a?.yearManufactured, a?.manufacturer, a?.model].filter(Boolean).join(" ");

  return (
    <div className="aircraft-profile-card">
      <div className="aircraft-profile-header">
        <div className="aircraft-profile-eyebrow">Aircraft profile</div>
        <div className="aircraft-profile-disclaimer-tag" title="Always shown — see disclaimer below">
          <ShieldAlert size={14} aria-hidden="true" />
          Owner ≠ pilot
        </div>
      </div>

      {loading && !profile && <div className="aircraft-profile-status">Loading registry data…</div>}
      {error && !profile && (
        <div className="aircraft-profile-status error">
          <AlertTriangle size={14} aria-hidden="true" /> Couldn't load registry data: {error}
        </div>
      )}

      {profile && (
        <>
          <div className="aircraft-profile-grid">
            <section className="aircraft-profile-section">
              <h4>Identity</h4>
              <dl>
                <Row label="Tail" value={profile.identity.nNumber} />
                <Row label="ICAO hex" value={profile.identity.icaoHex} />
                <Row label="Callsign" value={profile.identity.callsign} />
              </dl>
            </section>

            <section className="aircraft-profile-section">
              <h4>Aircraft</h4>
              <dl>
                <Row label="Make / model" value={yearLine || (a?.model ?? null)} />
                <Row label="Category" value={a?.category && a.category !== "Unknown" ? a.category : null} />
                <Row label="Type" value={a?.typeAircraft && a.typeAircraft !== "Unknown" ? a.typeAircraft : null} />
                <Row label="Engine" value={a?.engineType && a.engineType !== "Unknown" ? a.engineType : null} />
              </dl>
            </section>

            <section className="aircraft-profile-section">
              <h4>Registration</h4>
              <dl>
                <Row
                  label="Status"
                  value={r?.status ?? null}
                  badge={r?.isExpired || r?.isDeregistered ? "warning" : null}
                />
                <Row label="Expires" value={r?.expirationDate} />
                <Row label="Issued" value={r?.certificateIssueDate} />
              </dl>
              {(r?.isExpired || r?.isDeregistered) && (
                <div className="aircraft-profile-warning">
                  <AlertTriangle size={13} aria-hidden="true" />
                  {r.isDeregistered ? "Registration is cancelled / deregistered." : "Registration appears expired."}
                </div>
              )}
            </section>

            <section className="aircraft-profile-section">
              <h4>Registered owner</h4>
              <dl>
                <Row label="Name" value={o?.name ?? null} />
                <Row
                  label="Location"
                  value={[o?.city, o?.state].filter(Boolean).join(", ") || null}
                />
                <Row
                  label="Owner type"
                  value={
                    o?.name ? (
                      <span className={ownerBadgeClass(ownerType)} title={o?.ownerTypeReason ?? undefined}>
                        {ownerLabel}
                      </span>
                    ) : null
                  }
                />
              </dl>
              {o?.name && ownerType !== "unknown" && (
                <div className="aircraft-profile-owner-reason">
                  <Info size={12} aria-hidden="true" /> {o.ownerTypeReason}
                </div>
              )}
            </section>
          </div>

          <section className="aircraft-profile-airmen">
            <div className="aircraft-profile-airmen-title">Pilot identity</div>
            <p>
              Pilot identity cannot be determined from ADS-B or FAA aircraft registration alone.{" "}
              {profile.airmen.reason}
            </p>
            {profile.airmen.lookupAvailable && profile.airmen.lookupUrl && (
              <a
                className="aircraft-profile-airmen-link"
                href={profile.airmen.lookupUrl}
                target="_blank"
                rel="noreferrer"
              >
                <ExternalLink size={13} aria-hidden="true" />
                {profile.airmen.lookupLabel ?? "Search FAA Airmen Inquiry"}
              </a>
            )}
            <p className="aircraft-profile-airmen-disclaimer">{profile.airmen.disclaimer}</p>
          </section>

          <p className="aircraft-profile-owner-disclaimer">
            <strong>Note:</strong> {profile.disclaimers[0]}
          </p>
        </>
      )}
    </div>
  );
}

function Row({ label, value, badge }: { label: string; value: React.ReactNode; badge?: "warning" | null }) {
  if (value == null || value === "" || value === undefined) return null;
  return (
    <div className={`aircraft-profile-row${badge === "warning" ? " warning" : ""}`}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

export default AircraftProfileCard;
