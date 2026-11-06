/**
 * Tiny navigation context so any screen can switch the top-level route without
 * prop-drilling through <App>. E6-T1 owned the router; we extend it with the
 * onmodel / export / drafts routes added in T4/T5.
 */

import React, { createContext, useContext, useState } from "react";

export type Route = "capture" | "review" | "onmodel" | "export" | "drafts";

interface NavState {
  route: Route;
  navigate: (r: Route) => void;
}

const NavContext = createContext<NavState | null>(null);

export function useNav(): NavState {
  const ctx = useContext(NavContext);
  if (!ctx) throw new Error("useNav must be used within <NavProvider>");
  return ctx;
}

export function NavProvider(props: {
  children: React.ReactNode;
  initial?: Route;
}): JSX.Element {
  const [route, setRoute] = useState<Route>(props.initial ?? "capture");
  const value: NavState = { route, navigate: setRoute };
  return (
    <NavContext.Provider value={value}>{props.children}</NavContext.Provider>
  );
}
