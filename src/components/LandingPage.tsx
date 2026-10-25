import { useMemo } from 'react';
import { ArrowRight, ArrowUpRight, BarChart3, Boxes, MapPinned, PackageCheck, Route, ShieldCheck, Sparkles, TrendingUp, Warehouse } from 'lucide-react';
import { TerraDusk } from './TerraDusk';
import { INITIAL_BATCHES, INITIAL_FORECASTS, INITIAL_PRODUCTS, INITIAL_RETAILERS, INITIAL_ROUTES, INITIAL_WAREHOUSES } from '../data/mockData';
import { PLAN_HORIZON_DAYS, SYNTHETIC_SALES_PANEL, WORKSPACE_SEED_SCOPE } from '../data/demoScope';
import { runOptimizationEngine } from '../services/optimizationEngine';
import './LandingPage.css';

export function LandingPage() {
  const samplePlan = useMemo(() => runOptimizationEngine({
    products: INITIAL_PRODUCTS,
    warehouses: INITIAL_WAREHOUSES,
    retailers: INITIAL_RETAILERS,
    routes: INITIAL_ROUTES,
    batches: INITIAL_BATCHES,
    forecasts: INITIAL_FORECASTS,
  }), []);
  const sampleLine = samplePlan.lines[0];
  const sampleForecast = INITIAL_FORECASTS.find(forecast => forecast.retailerId === sampleLine?.retailerId && forecast.sku === sampleLine?.sku);
  const sampleProduct = INITIAL_PRODUCTS.find(product => product.sku === sampleLine?.sku);
  const sampleRetailer = INITIAL_RETAILERS.find(retailer => retailer.id === sampleLine?.retailerId);
  const sampleWarehouse = INITIAL_WAREHOUSES.find(warehouse => warehouse.id === sampleLine?.warehouseId);
  const sampleRoute = INITIAL_ROUTES.find(route => route.warehouseId === sampleLine?.warehouseId && route.retailerId === sampleLine?.retailerId);
  const sourceStock = sampleLine ? INITIAL_BATCHES
    .filter(batch => batch.warehouseId === sampleLine.warehouseId && batch.sku === sampleLine.sku)
    .reduce((total, batch) => total + Math.max(0, batch.qtyUnits - batch.reservedUnits), 0) : 0;

  return (
    <div className="landing-page">
      <header className="landing-nav">
        <a className="landing-brand" href="/" aria-label="HETVAYA home">
          <span className="landing-brand-mark"><Boxes size={21} strokeWidth={2.2} /></span>
          <span className="landing-brand-copy">
            <strong>HETVAYA</strong>
            <small>Distribution Intelligence</small>
          </span>
          <span className="landing-version">V1.5</span>
        </a>
        <div className="landing-nav-meta">
          <span><i /> Problem Statement 2</span>
          <span className="landing-meta-divider" />
              <span>FMCG · synthetic Maharashtra demo</span>
        </div>
        <a className="landing-nav-cta" href="/dashboard">
          Open workspace <ArrowRight size={16} />
        </a>
      </header>

      <main>
        <section className="landing-hero" aria-labelledby="landing-title">
          <div className="landing-hero-glow landing-glow-left" />
          <div className="landing-hero-glow landing-glow-right" />
          <div className="landing-grid-texture" />

          <div className="landing-copy">
            <div className="landing-eyebrows">
              <span className="landing-eyebrow landing-eyebrow-primary"><i /> Maharashtra Supply Grid</span>
              <span className="landing-eyebrow landing-eyebrow-cyan"><MapPinned size={14} /> {SYNTHETIC_SALES_PANEL.storeCount} stores · {SYNTHETIC_SALES_PANEL.skuCount} SKUs</span>
              <span className="landing-eyebrow landing-eyebrow-muted">{PLAN_HORIZON_DAYS}-day planning horizon</span>
            </div>

            <div className="landing-title-wrap">
              <p className="landing-kicker">Smarter stock movement, week by week</p>
              <h1 id="landing-title">AI-powered distribution intelligence</h1>
              <div className="landing-subtitle"><span /> Inventory optimization engine</div>
            </div>

            <p className="landing-description">
              Forecast store demand, balance available warehouse inventory, and create explainable replenishment plans with route cost and shelf-life constraints.
            </p>

            <div className="landing-capabilities" aria-label="Platform capabilities">
              <div><BarChart3 size={17} /><span>Seven-day demand ranges</span></div>
              <div><Boxes size={17} /><span>Inventory-aware allocation</span></div>
              <div><ShieldCheck size={17} /><span>Route and stock constraints</span></div>
            </div>

            <div className="landing-actions">
              <a className="landing-primary-action" href="/dashboard">
                <Sparkles size={17} /> Explore the planning workspace <ArrowRight size={17} />
              </a>
              <a className="landing-secondary-action" href="/dashboard#forecast">View forecasting tools</a>
            </div>

            <p className="landing-data-note">Demo environment · The forecasting panel is synthetic; local workspace examples use {WORKSPACE_SEED_SCOPE.retailerCount} retailer and {WORKSPACE_SEED_SCOPE.skuCount} SKU seed records.</p>
          </div>

          <div className="landing-visual" aria-label="Interactive earth visualization">
            <div className="landing-orbit landing-orbit-outer" />
            <div className="landing-orbit landing-orbit-inner" />
            <div className="landing-earth">
              <TerraDusk
                size={520}
                speed={7}
                sun="fixed"
                daylight={78}
                sunAngle={175}
                stars={5}
                hover={5}
                look={{ clouds: 5, lights: 5, atmosphere: '#4F9BFF' }}
                background="transparent"
                style={{ width: '100%', height: '100%' }}
              />
            </div>
            <div className="landing-location-tag"><i /> Pune distribution hub <span>18.5204° N · 73.8567° E</span></div>
            <div className="landing-visual-caption">Connected demand · stock · routes</div>
          </div>
        </section>

        <section className="landing-network-strip" aria-label="Demo network scale">
          <div><strong>{SYNTHETIC_SALES_PANEL.storeCount}</strong><span>sales-panel stores</span></div>
          <i />
          <div><strong>{SYNTHETIC_SALES_PANEL.skuCount}</strong><span>forecast SKU histories</span></div>
          <i />
          <div><strong>{SYNTHETIC_SALES_PANEL.warehouseCount}</strong><span>warehouse nodes</span></div>
          <p>{SYNTHETIC_SALES_PANEL.label}</p>
        </section>

        <section className="landing-workflow" aria-labelledby="workflow-title">
          <div className="landing-section-heading">
            <span className="landing-section-kicker"><i /> The planning workflow</span>
            <h2 id="workflow-title">From demand signal to dispatch plan.</h2>
            <p>Bring the decisions that shape replenishment into one reviewable flow.</p>
          </div>

          <div className="landing-feature-grid">
            <article className="landing-feature-card landing-feature-forecast">
              <div className="landing-feature-topline"><span>01 / FORECAST</span><TrendingUp size={17} /></div>
              <h3>See demand as a range.</h3>
              <p>Review seven-day retailer and SKU demand quantiles before committing stock.</p>
              <div className="landing-quantile-panel" aria-label="Illustrative forecast quantiles">
                <div><span>Q10 {sampleForecast?.q10 ?? '—'}</span><span>Q50 {sampleForecast?.q50 ?? '—'}</span><span>Q80 {sampleForecast?.q80 ?? '—'}</span><span>Q95 {sampleForecast?.q95 ?? '—'}</span></div>
                <div className="landing-quantile-track"><i /></div>
                <small>Sample workspace forecast · units / {PLAN_HORIZON_DAYS} days</small>
              </div>
            </article>

            <article className="landing-feature-card landing-feature-stock">
              <div className="landing-feature-topline"><span>02 / INVENTORY</span><Warehouse size={17} /></div>
              <h3>Allocate with constraints in view.</h3>
              <p>Balance usable warehouse stock against store demand, storage space, and shelf-life priorities.</p>
              <div className="landing-stock-flow">
                <span><PackageCheck size={14} /> Available lots</span>
                <b />
                <span><Boxes size={14} /> Store allocation</span>
              </div>
            </article>

            <article className="landing-feature-card landing-feature-route">
              <div className="landing-feature-topline"><span>03 / DISPATCH</span><Route size={17} /></div>
              <h3>Compare the movement plan.</h3>
              <p>Inspect warehouse-to-store recommendations with estimated freight and transit time.</p>
              <div className="landing-route-preview">
                <span>ORIGIN <b>Warehouse</b></span>
                <i><ArrowRight size={15} /></i>
                <span>DESTINATION <b>Retail store</b></span>
              </div>
            </article>
          </div>

          {sampleLine && sampleForecast && sampleRetailer && sampleWarehouse && sampleProduct && (
            <article className="landing-decision-card" aria-labelledby="decision-example-title">
              <div className="landing-decision-heading">
                <span className="landing-section-kicker"><i /> Explainable sample decision</span>
                <span className="landing-decision-source">Calculated from the local workspace seed</span>
              </div>
              <div className="landing-decision-body">
                <div className="landing-decision-summary">
                  <span className="landing-decision-label">RECOMMENDED ALLOCATION</span>
                  <h3 id="decision-example-title">{sampleProduct.name}</h3>
                  <p>{sampleWarehouse.name} <ArrowRight size={14} /> {sampleRetailer.name}</p>
                  <strong>{sampleLine.units.toLocaleString()} units <small>({sampleLine.cases} cases)</small></strong>
                </div>
                <div className="landing-decision-facts">
                  <div><span>7-day demand</span><strong>Q50 {sampleForecast.q50} <small>· Q95 {sampleForecast.q95}</small></strong></div>
                  <div><span>Source stock available</span><strong>{sourceStock.toLocaleString()} units</strong></div>
                  <div><span>Estimated freight · transit</span><strong>₹{sampleLine.freightRupees.toLocaleString()} <small>· {sampleRoute?.transitDays ?? '—'} days</small></strong></div>
                  <div><span>Arrival shelf life</span><strong>{sampleLine.explanation.shelfLifeOnArrivalPct}% <small>· minimum {sampleLine.explanation.minRequiredShelfLifePct}%</small></strong></div>
                </div>
              </div>
              {sampleLine.explanation.alternativeWarehouseCompared && sampleRoute && (
                <div className="landing-route-comparison">
                  <div className="landing-route-option landing-route-selected">
                    <span>RECOMMENDED ROUTE</span>
                    <strong>{sampleWarehouse.city} <ArrowRight size={13} /> {sampleRetailer.city}</strong>
                    <small>₹{sampleLine.freightRupees.toLocaleString()} freight · {sampleRoute.transitDays} day transit · shelf-life gate passed</small>
                  </div>
                  <span className="landing-route-vs">VS</span>
                  <div className="landing-route-option">
                    <span>NEXT ELIGIBLE ALTERNATIVE</span>
                    <strong>{sampleLine.explanation.alternativeWarehouseCompared.warehouseName}</strong>
                    <small>₹{Math.max(0, (sampleRoute.costPerCaseRupees + sampleLine.explanation.alternativeWarehouseCompared.freightDifferenceRupees) * sampleLine.cases).toLocaleString()} freight · {Math.max(0, sampleRoute.transitDays + sampleLine.explanation.alternativeWarehouseCompared.transitDaysDifference)} day transit</small>
                    <em>{sampleLine.explanation.alternativeWarehouseCompared.reasonNotChosen}</em>
                  </div>
                </div>
              )}
              <p className="landing-decision-reason">Why this route: the allocation engine compares retailer demand, available stock, modeled freight, transit time, and the retailer’s shelf-life threshold before selecting a source warehouse. The seed route data does not include road distance or vehicle capacity, so those are not claimed here.</p>
            </article>
          )}
        </section>

        <section className="landing-closing">
          <div>
            <span className="landing-section-kicker"><ShieldCheck size={14} /> HETVAYA WORKSPACE</span>
            <h2>Make the next replenishment decision with the full picture.</h2>
          </div>
          <a href="/dashboard">Explore the workspace <ArrowUpRight size={17} /></a>
        </section>

        <footer className="landing-footer">
          <span>HETVAYA <i /> Distribution Intelligence</span>
          <span>Sales, inventory, and route data in this demo are synthetic.</span>
          <a href="/dashboard">Open workspace <ArrowRight size={14} /></a>
        </footer>
      </main>
    </div>
  );
}

export default LandingPage;
