import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { airportLabel } from "./components/AirportPicker";
import { CitySidebar } from "./components/CitySidebar";
import { FlightPanel } from "./components/FlightPanel";
import { Heatmap } from "./components/Heatmap";
import { SettingsDialog } from "./components/SettingsDialog";
import { TopBar } from "./components/TopBar";
import { useAirportSearch, useStatus, useWeek } from "./hooks/queries";
import { useServerEvents } from "./hooks/useServerEvents";
import { api, type ViewParams } from "./lib/api";
import { useUrlState } from "./lib/urlState";

export default function App() {
  const [state, update] = useUrlState();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const qc = useQueryClient();

  const view: ViewParams = useMemo(
    () => ({ direction: state.direction, foreign: state.foreign, center: state.center }),
    [state.direction, state.foreign, state.center],
  );

  const week = useWeek(view, state.group, state.day);
  const { data: status } = useStatus();
  const { progress } = useServerEvents(state.direction, state.foreign);
  const { data: airportMatches } = useAirportSearch(state.foreign);
  const foreignLabel = airportLabel(
    airportMatches?.find((a) => a.iata === state.foreign),
    state.foreign,
  );

  // Default / repair the selected city once groups are known.
  const groups = week.data?.groups;
  useEffect(() => {
    if (groups?.length && !groups.some((g) => g.group_id === state.group)) {
      update({ group: groups[0]!.group_id });
    }
  }, [groups, state.group, update]);

  const invalidateAll = () => {
    void qc.invalidateQueries({ queryKey: ["week"] });
    void qc.invalidateQueries({ queryKey: ["flights"] });
    void qc.invalidateQueries({ queryKey: ["status"] });
  };
  const selectedDate = state.day === "week" ? undefined : state.day;
  const refreshWeek = useMutation({
    mutationFn: () => api.refreshWeek(view, state.group ?? undefined, selectedDate),
    onSuccess: invalidateAll,
  });
  const expireAll = useMutation({
    mutationFn: async () => {
      await api.expireAll();
      await api.refreshWeek(view, state.group ?? undefined, selectedDate);
    },
    onSuccess: invalidateAll,
  });
  const clearCache = useMutation({ mutationFn: api.clearCache, onSuccess: invalidateAll });

  const selectedGroup = groups?.find((g) => g.group_id === state.group);

  return (
    <div className="flex h-full flex-col">
      <TopBar
        direction={state.direction}
        foreign={state.foreign}
        center={state.center}
        progress={progress}
        status={status}
        onDirection={(direction) => update({ direction })}
        onForeign={(foreign) => update({ foreign })}
        onCenter={(center) => update({ center })}
        onRefreshWeek={() => refreshWeek.mutate()}
        onExpireAll={() => expireAll.mutate()}
        onClearCache={() => clearCache.mutate()}
        onOpenSettings={() => setSettingsOpen(true)}
      />

      {week.error ? (
        <div className="p-8 text-rose-600">
          Couldn't reach the backend: {(week.error as Error).message}. Is the API running on port 8000?
        </div>
      ) : !week.data ? (
        <div className="p-8 text-slate-500">Loading…</div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <CitySidebar
            groups={week.data.groups}
            selected={state.group}
            sort={state.citySort}
            onSort={(citySort) => update({ citySort })}
            onSelect={(group) => update({ group })}
          />
          <main className="flex min-h-0 flex-1 flex-col overflow-auto">
            <Heatmap
              week={week.data}
              selectedGroup={state.group}
              selectedDay={state.day}
              onSelect={(group, day) => update({ group, day })}
            />
            {selectedGroup && (
              <FlightPanel
                view={view}
                group={selectedGroup}
                foreignLabel={foreignLabel}
                day={state.day}
                sort={state.sort}
                onDay={(day) => update({ day })}
                onSort={(sort) => update({ sort })}
              />
            )}
          </main>
        </div>
      )}

      <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}
