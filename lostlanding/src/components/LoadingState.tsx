export function LoadingState() {
  return (
    <div className="page-state" role="status" aria-live="polite">
      <div className="inner">
        <div className="spinner" aria-hidden="true" />
        <h2>Counting runway uses…</h2>
        <p>Pulling the latest detected activity for KLMO from the ledger API.</p>
      </div>
    </div>
  );
}
