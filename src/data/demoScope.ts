import { INITIAL_PRODUCTS, INITIAL_RETAILERS, INITIAL_WAREHOUSES } from './mockData';

export const PLAN_HORIZON_DAYS = 7;

// These describe the generated forecasting panel included with the project.
export const SYNTHETIC_SALES_PANEL = {
  storeCount: 30,
  skuCount: 100,
  warehouseCount: 3,
  label: 'Synthetic sales panel',
} as const;

// The local UI fallback uses a smaller set of seed records than the full panel.
export const WORKSPACE_SEED_SCOPE = {
  retailerCount: INITIAL_RETAILERS.length,
  skuCount: INITIAL_PRODUCTS.length,
  warehouseCount: INITIAL_WAREHOUSES.length,
  label: 'Workspace seed scenario',
} as const;
