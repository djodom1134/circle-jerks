/**
 * The illustrative fee-per-runway-use slider's value, lifted out of
 * Calculator so the live map's price tags and the hero ticker can all read
 * (and react to) the SAME number the user is dragging -- one source of
 * truth, never three independent copies that could drift apart.
 */
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

export const DEFAULT_FEE = 10;

interface FeeContextValue {
  fee: number;
  setFee: (fee: number) => void;
}

const FeeContext = createContext<FeeContextValue | null>(null);

export function FeeProvider({ children, initialFee = DEFAULT_FEE }: { children: ReactNode; initialFee?: number }) {
  const [fee, setFee] = useState(initialFee);
  const value = useMemo(() => ({ fee, setFee }), [fee]);
  return <FeeContext.Provider value={value}>{children}</FeeContext.Provider>;
}

export function useFee(): FeeContextValue {
  const ctx = useContext(FeeContext);
  if (!ctx) {
    throw new Error("useFee() must be called within a <FeeProvider>");
  }
  return ctx;
}
