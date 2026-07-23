import type { DailyOperations, OriginsResponse, WorstOffendersResponse, HourlyProfileResponse } from "../lib/types";
import { daysForMonth, isMonthInCoverage, type FilterState, type ColorBy } from "../lib/facets";
import { KpiRow } from "./KpiRow";
import { DayBarChart } from "./DayBarChart";
import { TrendPanel } from "./TrendPanel";
import { AircraftPanel } from "./AircraftPanel";
import { HourProfilePanel } from "./HourProfilePanel";
import { OriginHub } from "./OriginHub";
import { EmptyState } from "./EmptyState";

interface DashboardPageProps {
  data: DailyOperations;
  origins: OriginsResponse | null;
  offenders: WorstOffendersResponse | null;
  hourly: HourlyProfileResponse | null;
  airportLabel: string;
  month: string;
  filter: FilterState;
  colorBy: ColorBy;
  dark: boolean;
  originCount: number;
  selectedOrigin: string | null;
  onSelectOrigin(icao: string | null): void;
}

export function DashboardPage(props: DashboardPageProps) {
  const { data, month, filter, colorBy, dark } = props;
  const days = daysForMonth(data, month);
  const inCoverage = isMonthInCoverage(data, month);

  return (
    <main>
      {inCoverage ? (
        <>
          <KpiRow days={days} filter={filter} originCount={props.originCount} />
          <DayBarChart days={days} filter={filter} colorBy={colorBy} dark={dark} />
        </>
      ) : (
        <EmptyState month={month} />
      )}
      <div className="panel-row">
        <TrendPanel data={data} activeMonth={month} />
        <HourProfilePanel hourly={props.hourly} />
        <AircraftPanel offenders={props.offenders} />
      </div>
      <OriginHub origins={props.origins} selectedOrigin={props.selectedOrigin}
        onSelectOrigin={props.onSelectOrigin} airportIcao={data.airport_icao} />
    </main>
  );
}
