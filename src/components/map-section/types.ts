/** One location record as returned by the data source. */
export interface RegionStat {
  id: string;
  name: string;
  city: string;
  lat: number;
  lng: number;
  /** Metric shown in the bars and used for marker size. */
  value: number;
  category: string;
}

/** A record decorated with its overall rank and display color. */
export interface RankedRegionStat extends RegionStat {
  /** 1-based rank by value across the full dataset (not affected by filters). */
  rank: number;
  color: string;
}

export type SortKey = "name" | "value";
export type SortDir = "asc" | "desc";

export interface SortState {
  key: SortKey;
  dir: SortDir;
}

/** Sentinel for "no category filter". */
export const ALL_CATEGORIES = "all";

export type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "success"; data: RegionStat[] };

/** Bumped each time a table row asks the map to fly to a marker. */
export interface FlyRequest {
  id: string;
  nonce: number;
}
