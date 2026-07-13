export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="page-state" role="alert">
      <div className="inner">
        <p className="kicker">Ledger unavailable</p>
        <h2>We couldn't load the runway-use ledger.</h2>
        <p>{message}</p>
        <p>
          This is a data-availability problem, not a finding — we don't publish numbers we can't fetch. Try again in a
          moment.
        </p>
        <button type="button" className="retry-button" onClick={onRetry}>
          Retry
        </button>
      </div>
    </div>
  );
}
