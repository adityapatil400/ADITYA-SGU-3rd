import { useEffect, useRef } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, Search } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import {
  ALL_CATEGORIES,
  type RankedRegionStat,
  type SortKey,
  type SortState,
} from "./types";

interface RankedBarTableProps {
  rows: RankedRegionStat[];
  totalCount: number;
  maxValue: number;
  totalValue: number;
  formatValue: (n: number) => string;
  valueLabel: string;
  categoryLabel: string;
  categories: string[];
  query: string;
  onQueryChange: (q: string) => void;
  category: string;
  onCategoryChange: (c: string) => void;
  onClearFilters: () => void;
  sort: SortState;
  onSortChange: (key: SortKey) => void;
  hoveredId: string | null;
  selectedId: string | null;
  onHover: (id: string | null) => void;
  onSelect: (id: string) => void;
}

const COLS = 5;

export function RankedBarTable({
  rows,
  totalCount,
  maxValue,
  totalValue,
  formatValue,
  valueLabel,
  categoryLabel,
  categories,
  query,
  onQueryChange,
  category,
  onCategoryChange,
  onClearFilters,
  sort,
  onSortChange,
  hoveredId,
  selectedId,
  onHover,
  onSelect,
}: RankedBarTableProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // When a marker is selected, scroll its row into view inside the panel only
  // (scrollIntoView would also scroll the page on mobile).
  useEffect(() => {
    const container = scrollRef.current?.querySelector<HTMLElement>(
      "[data-slot=table-container]",
    );
    if (!container || !selectedId) return;
    const row = container.querySelector<HTMLElement>(
      `[data-row-id="${CSS.escape(selectedId)}"]`,
    );
    const head = container.querySelector<HTMLElement>("thead");
    const foot = container.querySelector<HTMLElement>("tfoot");
    if (!row) return;
    const top = row.offsetTop - (head?.offsetHeight ?? 0);
    const bottom =
      row.offsetTop + row.offsetHeight - container.clientHeight + (foot?.offsetHeight ?? 0);
    if (container.scrollTop > top) container.scrollTop = top;
    else if (container.scrollTop < bottom) container.scrollTop = bottom;
  }, [selectedId]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex flex-col gap-2 sm:flex-row">
        <div className="relative flex-1">
          <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
          <Input
            type="search"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder="Search by name…"
            aria-label="Search by name"
            className="pl-8"
          />
        </div>
        <Select value={category} onValueChange={onCategoryChange}>
          <SelectTrigger className="w-full sm:w-40" aria-label={`Filter by ${categoryLabel}`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_CATEGORIES}>All {categoryLabel.toLowerCase()}s</SelectItem>
            {categories.map((c) => (
              <SelectItem key={c} value={c}>
                {c}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div
        ref={scrollRef}
        className={cn(
          "min-h-0 flex-1 rounded-lg border",
          // Make shadcn's table wrapper the vertical scroller so the header can stick.
          "*:data-[slot=table-container]:h-full *:data-[slot=table-container]:max-h-[420px] *:data-[slot=table-container]:overflow-auto lg:*:data-[slot=table-container]:max-h-none",
        )}
      >
        <Table className="table-fixed">
          <TableHeader className="bg-card sticky top-0 z-10 shadow-[inset_0_-1px_0_var(--border)]">
            <TableRow className="hover:bg-transparent">
              <TableHead className="w-9 text-right">#</TableHead>
              <TableHead aria-sort={ariaSort(sort, "name")} className="w-[44%] sm:w-[33%]">
                <SortButton label="Name" sortKey="name" sort={sort} onSortChange={onSortChange} />
              </TableHead>
              <TableHead className="hidden w-[27%] sm:table-cell">{categoryLabel}</TableHead>
              <TableHead className="w-auto">
                <span className="sr-only">{valueLabel} bar</span>
              </TableHead>
              <TableHead aria-sort={ariaSort(sort, "value")} className="w-20 text-right">
                <SortButton
                  label="Value"
                  sortKey="value"
                  sort={sort}
                  onSortChange={onSortChange}
                  align="right"
                />
              </TableHead>
            </TableRow>
          </TableHeader>

          <TableBody>
            {rows.length === 0 ? (
              <TableRow className="hover:bg-transparent">
                <TableCell colSpan={COLS} className="h-40 text-center whitespace-normal">
                  <p className="text-muted-foreground">No results</p>
                  {(query || category !== ALL_CATEGORIES) && (
                    <Button variant="link" size="sm" onClick={onClearFilters}>
                      Clear filters
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ) : (
              rows.map((row) => {
                const pct = maxValue > 0 ? (row.value / maxValue) * 100 : 0;
                const isHovered = row.id === hoveredId;
                const isSelected = row.id === selectedId;
                return (
                  <TableRow
                    key={row.id}
                    data-row-id={row.id}
                    data-state={isSelected ? "selected" : undefined}
                    aria-selected={isSelected}
                    tabIndex={0}
                    onMouseEnter={() => onHover(row.id)}
                    onMouseLeave={() => onHover(null)}
                    onFocus={() => onHover(row.id)}
                    onBlur={() => onHover(null)}
                    onClick={() => onSelect(row.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelect(row.id);
                      }
                    }}
                    className={cn(
                      "focus-visible:ring-ring cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-inset",
                      isHovered && "bg-muted/60",
                      isSelected && "bg-primary/10 hover:bg-primary/15",
                    )}
                  >
                    <TableCell className="text-muted-foreground text-right tabular-nums">
                      {row.rank}
                    </TableCell>
                    <TableCell className="max-w-0">
                      <div className="truncate font-medium" title={row.name}>
                        {row.name}
                      </div>
                      <div className="text-muted-foreground flex items-center gap-1.5 truncate text-xs">
                        <span
                          className="size-2 shrink-0 rounded-full sm:hidden"
                          style={{ backgroundColor: row.color }}
                          aria-hidden
                        />
                        <span className="sm:hidden">{row.category} · </span>
                        {row.city}
                      </div>
                    </TableCell>
                    <TableCell className="hidden text-xs sm:table-cell">
                      <span className="flex items-center gap-1.5" title={row.category}>
                        <span
                          className="size-2 shrink-0 rounded-full"
                          style={{ backgroundColor: row.color }}
                          aria-hidden
                        />
                        <span className="truncate">{row.category}</span>
                      </span>
                    </TableCell>
                    <TableCell>
                      <div
                        className="bg-muted h-2.5 w-full overflow-hidden rounded-full"
                        role="presentation"
                      >
                        <div
                          className="h-full rounded-full transition-[width] duration-300 ease-out"
                          style={{
                            width: `${row.value > 0 ? Math.max(pct, 2) : 0}%`,
                            backgroundColor: row.color,
                          }}
                        />
                      </div>
                    </TableCell>
                    <TableCell className="text-right font-medium tabular-nums">
                      {formatValue(row.value)}
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>

          <TableFooter className="bg-card sticky bottom-0 z-10 shadow-[inset_0_1px_0_var(--border)]">
            <TableRow className="hover:bg-transparent">
              <TableCell colSpan={2} className="text-muted-foreground font-normal">
                Showing {rows.length} of {totalCount}
              </TableCell>
              <TableCell className="hidden sm:table-cell" />
              <TableCell className="text-muted-foreground text-right font-normal">Total</TableCell>
              <TableCell className="text-right tabular-nums">{formatValue(totalValue)}</TableCell>
            </TableRow>
          </TableFooter>
        </Table>
      </div>
    </div>
  );
}

function ariaSort(sort: SortState, key: SortKey): "ascending" | "descending" | "none" {
  if (sort.key !== key) return "none";
  return sort.dir === "asc" ? "ascending" : "descending";
}

interface SortButtonProps {
  label: string;
  sortKey: SortKey;
  sort: SortState;
  onSortChange: (key: SortKey) => void;
  align?: "left" | "right";
}

function SortButton({ label, sortKey, sort, onSortChange, align = "left" }: SortButtonProps) {
  const active = sort.key === sortKey;
  const Icon = !active ? ArrowUpDown : sort.dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <button
      type="button"
      onClick={() => onSortChange(sortKey)}
      className={cn(
        "hover:text-foreground focus-visible:ring-ring inline-flex items-center gap-1 rounded-sm outline-none focus-visible:ring-2",
        align === "right" && "flex-row-reverse",
        active ? "text-foreground" : "text-muted-foreground",
      )}
    >
      {label}
      <Icon className="size-3.5" aria-hidden />
    </button>
  );
}

export function RankedBarTableSkeleton() {
  return (
    <div className="flex h-full flex-col gap-3" aria-busy="true" aria-label="Loading table">
      <div className="flex flex-col gap-2 sm:flex-row">
        <Skeleton className="h-9 flex-1" />
        <Skeleton className="h-9 w-full sm:w-40" />
      </div>
      <div className="flex-1 space-y-3 rounded-lg border p-3">
        <Skeleton className="h-4 w-1/3" />
        {Array.from({ length: 8 }, (_, i) => (
          <div key={i} className="flex items-center gap-3">
            <Skeleton className="h-4 w-5" />
            <Skeleton className="h-4 w-[35%]" />
            <Skeleton className="h-2.5 flex-1 rounded-full" />
            <Skeleton className="h-4 w-12" />
          </div>
        ))}
      </div>
    </div>
  );
}
