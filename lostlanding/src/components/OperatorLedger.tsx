import type { LedgerResponse, Operator } from "../lib/types";
import { formatInteger } from "../lib/format";

const LOCALITY_LABEL: Record<Operator["locality"], string> = {
  local: "Local",
  non_local: "Non-local",
  unclassified: "Unclassified",
};

function OperatorRow({ operator }: { operator: Operator }) {
  const isPrivateBucket = operator.owner_type === "private";
  const sortedAircraft = [...operator.aircraft].sort((a, b) => a.tail.localeCompare(b.tail));

  return (
    <details className="operator-row">
      <summary>
        <span className="operator-name">{operator.operator}</span>
        <span className="operator-stat">
          <span className="num">{formatInteger(operator.runway_uses)}</span>
          <span className="stat-label">Runway uses</span>
        </span>
        <span className="operator-stat">
          <span className="num">{formatInteger(operator.aircraft_count)}</span>
          <span className="stat-label">Aircraft</span>
        </span>
        <span
          className={`badge locality-${operator.locality}${isPrivateBucket ? " owner-private" : ""}`}
        >
          {isPrivateBucket ? "Private / unaffiliated" : LOCALITY_LABEL[operator.locality]}
        </span>
      </summary>

      <div className="aircraft-detail">
        {sortedAircraft.length > 0 ? (
          <table>
            <caption className="visually-hidden">Aircraft under {operator.operator}, alphabetical by tail</caption>
            <thead>
              <tr>
                <th scope="col">Tail</th>
                <th scope="col">Runway uses</th>
              </tr>
            </thead>
            <tbody>
              {sortedAircraft.map((aircraft) => (
                <tr key={aircraft.tail}>
                  <td>{aircraft.tail}</td>
                  <td>{formatInteger(aircraft.runway_uses)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p>No individual aircraft listed for this bucket.</p>
        )}

        {operator.locality_evidence.length > 0 && (
          <ul className="evidence-list">
            {operator.locality_evidence.map((evidence) => (
              <li key={evidence.code}>{evidence.text}</li>
            ))}
          </ul>
        )}
      </div>
    </details>
  );
}

export function OperatorLedger({ data }: { data: LedgerResponse }) {
  const operators = [...data.operators].sort((a, b) => b.runway_uses - a.runway_uses);

  return (
    <section id="ledger" className="section-pad">
      <div className="section-heading">
        <p className="kicker">The Operator Ledger</p>
        <h2>Organizations named. Individuals bucketed.</h2>
        <p>
          Every row below is either a named organization or the aggregate "Private / unaffiliated" bucket. We do not
          publish a ranking of individual private tail numbers — an N-number resolves to a person's name and home
          address in one FAA lookup, and some of the aircraft on this field are students in rented planes.
        </p>
      </div>

      {operators.length === 0 ? (
        <div className="empty-state">
          <p className="kicker">No operators recorded</p>
          <p>
            No runway uses were attributed to any operator in this window
            {data.summary.runway_uses === 0 ? " — the total runway-use count for this window is also zero." : "."}
          </p>
        </div>
      ) : (
        <div className="operator-list">
          {operators.map((operator) => (
            <OperatorRow key={operator.operator} operator={operator} />
          ))}
        </div>
      )}
    </section>
  );
}
