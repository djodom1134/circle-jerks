/**
 * The fair-use calculator's arithmetic, isolated from rendering.
 *
 * CRITICAL: this multiplies the fee by RUNWAY USES (landings + touch-and-gos
 * + low approaches), never by FAA "operations" (takeoffs + landings). An
 * operation is one takeoff OR one landing, so a touch-and-go -- one runway
 * use -- counts as two operations. Multiplying a fee by an operations count
 * would double the true billable total for every touch-and-go on the field.
 * See calculator.test.ts for a test that proves this does not happen.
 */

export interface CalculatorInputs {
  /** summary.runway_uses observed over `windowDays` -- never an operations count. */
  runwayUses: number;
  /** window.days the runwayUses figure was observed over. */
  windowDays: number;
  /** Illustrative fee, in dollars, per runway use. */
  feePerUse: number;
  /** Illustrative share of runway uses assumed billable, 0..1. Defaults to 100%. */
  billableShare?: number;
}

export interface CalculatorResult {
  /** Observed runway uses per day, from real data (not assumed). */
  dailyRate: number;
  perDay: number;
  perMonth: number;
  perYear: number;
  fiveYear: number;
  tenYear: number;
}

const DAYS_PER_MONTH = 30;
const DAYS_PER_YEAR = 365;

export function computeRevenue(inputs: CalculatorInputs): CalculatorResult {
  const { runwayUses, windowDays, feePerUse, billableShare = 1 } = inputs;

  const dailyRate = windowDays > 0 ? runwayUses / windowDays : 0;
  const perDay = dailyRate * feePerUse * billableShare;
  const perMonth = perDay * DAYS_PER_MONTH;
  const perYear = perDay * DAYS_PER_YEAR;

  return {
    dailyRate,
    perDay,
    perMonth,
    perYear,
    fiveYear: perYear * 5,
    tenYear: perYear * 10,
  };
}
