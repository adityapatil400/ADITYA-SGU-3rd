/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Optional endpoint returning RegionStat[] for the Map section. */
  readonly VITE_REGION_STATS_URL?: string;
}
