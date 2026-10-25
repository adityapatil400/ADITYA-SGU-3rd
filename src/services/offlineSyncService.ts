import { SyncEvent, RejectionReason } from '../types';

const STORAGE_KEY = 'hetvaya_offline_event_queue';
const DEVICE_ID_KEY = 'hetvaya_device_id';

export function getDeviceId(): string {
  let deviceId = localStorage.getItem(DEVICE_ID_KEY);
  if (!deviceId) {
    deviceId = `DEV-${Math.random().toString(36).substring(2, 9).toUpperCase()}`;
    localStorage.setItem(DEVICE_ID_KEY, deviceId);
  }
  return deviceId;
}

export function getStoredEventQueue(): SyncEvent[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function saveEventQueue(queue: SyncEvent[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(queue));
  } catch (e) {
    console.error('Failed to save offline queue', e);
  }
}

export function enqueueEvent(
  type: SyncEvent['type'],
  payload: any,
  isOnline: boolean,
  onSyncComplete?: (event: SyncEvent) => void
): SyncEvent {
  const deviceId = getDeviceId();
  const queue = getStoredEventQueue();
  const nextSeq = queue.length > 0 ? Math.max(...queue.map(e => e.deviceSeq)) + 1 : 1;

  const newEvent: SyncEvent = {
    eventId: `EVT-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`,
    deviceId,
    deviceSeq: nextSeq,
    type,
    occurredAt: new Date().toISOString(),
    payload,
    status: isOnline ? 'APPLIED' : 'QUEUED',
  };

  const updatedQueue = [...queue, newEvent];
  saveEventQueue(updatedQueue);

  if (isOnline && onSyncComplete) {
    onSyncComplete(newEvent);
  }

  return newEvent;
}

export interface SyncResult {
  syncedCount: number;
  supersededCount: number;
  duplicateCount: number;
  syncTimeMs: number;
  processedEvents: SyncEvent[];
}

export function processEventQueue(
  existingEvents: SyncEvent[],
  existingBatchesAsOfMap: Map<string, string>
): SyncResult {
  const startTime = performance.now();
  let synced = 0;
  let superseded = 0;
  let duplicates = 0;

  const seenEventIds = new Set<string>();

  const processed = existingEvents.map(evt => {
    if (seenEventIds.has(evt.eventId)) {
      duplicates++;
      return { ...evt, status: 'DUPLICATE' as const, reason: 'Event ID already recorded in database' };
    }
    seenEventIds.add(evt.eventId);

    if (evt.type === 'STOCK_COUNT') {
      const batchOrRetailerKey = evt.payload.batchId || evt.payload.retailerId;
      const currentAsOf = existingBatchesAsOfMap.get(batchOrRetailerKey);
      if (currentAsOf && new Date(evt.payload.asOf) <= new Date(currentAsOf)) {
        superseded++;
        return {
          ...evt,
          status: 'SUPERSEDED' as const,
          reason: 'Count timestamp is older than current snapshot (Section 15.4)',
        };
      }
    }

    synced++;
    return { ...evt, status: 'APPLIED' as const };
  });

  saveEventQueue(processed);

  return {
    syncedCount: synced,
    supersededCount: superseded,
    duplicateCount: duplicates,
    syncTimeMs: Math.round(performance.now() - startTime),
    processedEvents: processed,
  };
}
