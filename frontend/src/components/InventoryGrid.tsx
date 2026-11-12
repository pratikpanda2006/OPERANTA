
import React, { useMemo, useState } from 'react';

import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type ColDef,
} from 'ag-grid-community';
import { AgGridReact } from 'ag-grid-react';

import { AgCharts } from 'ag-charts-react';
import {
  AllCommunityModule as AllChartsCommunityModule,
  ModuleRegistry as ChartsModuleRegistry,
  type AgChartOptions,
  type AgBarSeriesOptions,
} from 'ag-charts-community';

import { InventoryList, InventoryItem } from '../api/types';

// Register AG Grid and AG Charts Community features.
ModuleRegistry.registerModules([AllCommunityModule]);
ChartsModuleRegistry.registerModules([AllChartsCommunityModule]);

interface InventoryGridProps {
  inventory: InventoryList | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

function formatPrice(
  val: string | number | null | undefined,
): string {
  if (val === null || val === undefined) return '—';

  const num = typeof val === 'number' ? val : parseFloat(val);

  if (isNaN(num)) return '—';

  return `$${num.toFixed(2)}`;
}

// OPERANTA dark AG Grid theme.
const inventoryTheme = themeQuartz.withParams({
  accentColor: '#8b7cf6',
  backgroundColor: '#111827',
  foregroundColor: '#e5e7eb',
  headerBackgroundColor: '#1c2540',
  headerTextColor: '#cbd5e1',
  borderColor: '#293449',
  spacing: 8,
  fontSize: 13,
  headerFontSize: 12,
});

export const InventoryGrid: React.FC<InventoryGridProps> = ({
  inventory,
  loading,
  error,
  onRetry,
}) => {
  const [quickFilter, setQuickFilter] = useState('');

  const items: InventoryItem[] = inventory?.items ?? [];

  const lowStockCount =
    inventory?.low_stock_count ??
    items.filter((item) => item.reorder_recommended).length;

  // AG Charts: live available stock versus reorder threshold.
  const chartOptions = useMemo<AgChartOptions>(() => {
    const chartData = items.map((item) => ({
      product: item.product_name,
      available: Number(item.total_available) || 0,
      reorderPoint: Number(item.reorder_point) || 0,
    }));

    return {
      theme: {
        baseTheme: 'ag-default-dark',
        params: {
          backgroundColor: '#111827',
          foregroundColor: '#e5e7eb',
          accentColor: '#8b7cf6',
        },
      },
      data: chartData,
      title: {
        text: 'Available Stock vs Reorder Point',
      },
      series: [
        {
          type: 'bar',
          xKey: 'product',
          yKey: 'available',
          yName: 'Available Stock',
          fill: '#8b7cf6',
        },
        {
          type: 'bar',
          xKey: 'product',
          yKey: 'reorderPoint',
          yName: 'Reorder Threshold',
          fill: '#f59e0b',
        },
      ] as AgBarSeriesOptions[],
      axes: {
        x: {
          type: 'category',
          title: { text: 'Product' },
        },
        y: {
          type: 'number',
          title: { text: 'Units' },
          min: 0,
        },
      },
      legend: {
        enabled: true,
        position: 'bottom',
      },
    };
  }, [items]);

  // AG Grid columns.
  const columnDefs = useMemo<ColDef<InventoryItem>[]>(
    () => [
      {
        field: 'product_name',
        headerName: 'Product',
        pinned: 'left',
        minWidth: 175,
        flex: 1.4,
      },
      {
        field: 'sku',
        headerName: 'SKU',
        minWidth: 115,
      },
      {
        field: 'category',
        headerName: 'Category',
        minWidth: 125,
      },
      {
        field: 'retail_price',
        headerName: 'Retail Price',
        minWidth: 115,
        valueFormatter: (params) => formatPrice(params.value),
      },
      {
        field: 'base_cost',
        headerName: 'Base Cost',
        minWidth: 110,
        valueFormatter: (params) => formatPrice(params.value),
      },
      {
        field: 'available_in_store',
        headerName: 'Store Available',
        minWidth: 125,
      },
      {
        field: 'stock_in_store',
        headerName: 'Store On Hand',
        minWidth: 120,
      },
      {
        field: 'available_online',
        headerName: 'Online Available',
        minWidth: 130,
      },
      {
        field: 'stock_online',
        headerName: 'Online On Hand',
        minWidth: 125,
      },
      {
        field: 'total_available',
        headerName: 'Total Available',
        minWidth: 125,
        cellStyle: (params) =>
          params.data?.reorder_recommended
            ? { color: '#fbbf24', fontWeight: '700' }
            : { color: '#34d399', fontWeight: '700' },
      },
      {
        field: 'reorder_point',
        headerName: 'Reorder Point',
        minWidth: 115,
      },
      {
        field: 'daily_demand',
        headerName: 'Daily Demand',
        minWidth: 115,
        valueFormatter: (params) =>
          params.value == null
            ? '—'
            : `${Number(params.value).toFixed(1)}/day`,
      },
      {
        headerName: 'Stock Status',
        colId: 'stock_status',
        minWidth: 145,
        valueGetter: (params) =>
          params.data?.reorder_recommended
            ? 'Reorder Needed'
            : 'Optimal',
        cellRenderer: (params: { value: string }) => (
          <span
            className={`badge ${params.value === 'Reorder Needed'
                ? 'badge-warning pulse-subtle'
                : 'badge-success'
              }`}
          >
            {params.value}
          </span>
        ),
      },
    ],
    [],
  );

  const defaultColDef = useMemo<ColDef<InventoryItem>>(
    () => ({
      sortable: true,
      filter: true,
      resizable: true,
      minWidth: 100,
    }),
    [],
  );

  // Loading state.
  if (loading) {
    return (
      <article className="dashboard-card inventory-card" aria-busy="true">
        <div className="card-header">
          <div className="card-title-group">
            <span className="card-icon" aria-hidden="true">📦</span>
            <h3>Omnichannel Inventory & Stock Levels</h3>
          </div>
          <span className="badge badge-neutral">Loading Stock...</span>
        </div>
        <div className="card-skeleton table-skeleton">
          <div className="skeleton-row"></div>
          <div className="skeleton-row"></div>
          <div className="skeleton-row"></div>
        </div>
      </article>
    );
  }

  // API error state.
  if (error) {
    return (
      <article
        className="dashboard-card inventory-card card-error-state"
        role="alert"
      >
        <div className="card-header">
          <div className="card-title-group">
            <span className="card-icon" aria-hidden="true">📦</span>
            <h3>Omnichannel Inventory & Stock Levels</h3>
          </div>
          <span className="badge badge-danger">Sync Error</span>
        </div>

        <div className="card-error-body">
          <p className="card-error-message">{error}</p>
          <button className="btn-secondary" onClick={onRetry}>
            🔄 Retry Inventory Sync
          </button>
        </div>
      </article>
    );
  }

  return (
    <article className="dashboard-card inventory-card">
      {/* Section header */}
      <div className="card-header">
        <div className="card-title-group">
          <span className="card-icon" aria-hidden="true">📦</span>
          <div>
            <h3>Omnichannel Inventory & Stock Levels</h3>
            <span className="card-subtitle">
              Unified In-Store & Online Stock Synchronization
            </span>
          </div>
        </div>

        <div className="header-badges">
          {lowStockCount > 0 ? (
            <span className="badge badge-warning">
              ⚠️ {lowStockCount} Reorder{' '}
              {lowStockCount === 1 ? 'Alert' : 'Alerts'}
            </span>
          ) : (
            <span className="badge badge-success">
              ✓ Stock Healthy
            </span>
          )}

          <span className="badge badge-neutral">
            {items.length} Active {items.length === 1 ? 'SKU' : 'SKUs'}
          </span>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="empty-state">
          <p>No inventory records on file for this merchant.</p>
        </div>
      ) : (
        <>
          {/* AG CHARTS: Inventory stock health */}
          <section
            style={{
              background: '#111827',
              border: '1px solid #293449',
              borderRadius: '12px',
              padding: '16px',
              marginBottom: '18px',
            }}
          >
            <div style={{ marginBottom: '8px' }}>
              <h4 style={{ color: '#e5e7eb', margin: 0 }}>
                Inventory Stock Health
              </h4>
              <span
                style={{
                  color: '#94a3b8',
                  fontSize: '12px',
                }}
              >
                Compare available inventory against reorder thresholds
              </span>
            </div>

            <div style={{ width: '100%', height: '280px' }}>
              <AgCharts options={chartOptions} />
            </div>
          </section>

          {/* Search and grid controls */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              gap: '12px',
              flexWrap: 'wrap',
              marginBottom: '14px',
            }}
          >
            <input
              type="search"
              aria-label="Search inventory"
              placeholder="Search products, SKU, category..."
              value={quickFilter}
              onChange={(event) => setQuickFilter(event.target.value)}
              style={{
                background: '#090e19',
                border: '1px solid #293449',
                borderRadius: '8px',
                color: '#e5e7eb',
                padding: '10px 14px',
                width: 'min(100%, 340px)',
                outline: 'none',
                fontSize: '13px',
              }}
            />

            <span className="badge badge-neutral">
              Sortable · Filterable · Resizable
            </span>
          </div>

          {/* AG GRID: Inventory data */}
          <div
            style={{
              width: '100%',
              height: '460px',
              borderRadius: '10px',
              overflow: 'hidden',
            }}
          >
            <AgGridReact<InventoryItem>
              theme={inventoryTheme}
              rowData={items}
              columnDefs={columnDefs}
              defaultColDef={defaultColDef}
              quickFilterText={quickFilter}
              pagination={true}
              paginationPageSize={8}
              animateRows={true}
              rowHeight={48}
              headerHeight={44}
              getRowId={(params) => params.data.id}
              getRowStyle={(params) =>
                params.data?.reorder_recommended
                  ? { backgroundColor: 'rgba(245, 158, 11, 0.07)' }
                  : undefined
              }
            />
          </div>
        </>
      )}
    </article>
  );
};
