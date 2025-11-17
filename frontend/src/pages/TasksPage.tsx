import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  LinearProgress,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useApi } from '../contexts/ServiceContext';

interface JobResponse {
  id: string;
  job_type: string;
  status: string;
  progress_percent: number;
  message?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  current_stage?: string | null;
  result?: Record<string, unknown> | null;
}

interface JobListResponse {
  jobs: JobResponse[];
  total: number;
}

interface JobEventPayload {
  job_id: string;
  job_type?: string;
  status?: string;
  current_stage?: string | null;
  stage?: string | null;
  message?: string | null;
  progress_percent?: number | null;
  progress_bucket?: number | null;
  event_type?: string;
  timestamp?: string;
  data?: Record<string, unknown>;
}

const STATUS_TO_COLOR: Record<string, 'default' | 'primary' | 'success' | 'error' | 'warning' | 'info'> = {
  queued: 'default',
  waiting: 'info',
  running: 'primary',
  completed: 'success',
  failed: 'error',
  canceled: 'warning',
};

const JOB_TYPE_LABELS: Record<string, string> = {
  'ollama.install': 'Model Install',
  'system.sleep': 'System Check',
};

const TERMINAL_JOB_STATUSES = new Set(['completed', 'failed', 'canceled']);

const formatDateTime = (value?: string | null) => {
  if (!value) {
    return '—';
  }
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(value));
  } catch (error) {
    console.warn('Failed to format datetime', error);
    return value;
  }
};

const TasksPage: React.FC = () => {
  const apiService = useApi();
  const [jobs, setJobs] = useState<JobResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<Record<string, string>>({});
  const streamControllers = useRef<Record<string, () => void>>({});

  const fetchJobs = useCallback(async () => {
    if (!apiService) {
      return;
    }
    setError(null);
    setRefreshing(true);
    try {
      const data = (await apiService.get('/api/v1/jobs?page=1&page_size=50')) as JobListResponse;
      setJobs(data?.jobs ?? []);
    } catch (err) {
      console.error('Failed to load jobs', err);
      setError('We could not load your tasks right now. Please try again in a moment.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [apiService]);

  useEffect(() => {
    fetchJobs();
    const interval = setInterval(fetchJobs, 10000);
    return () => clearInterval(interval);
  }, [fetchJobs]);

  useEffect(() => {
    window.currentPageTitle = 'My Tasks';
    return () => {
      window.currentPageTitle = undefined;
    };
  }, []);

  const stopStream = useCallback((jobId: string) => {
    const disposer = streamControllers.current[jobId];
    if (!disposer) {
      return;
    }
    delete streamControllers.current[jobId];
    try {
      disposer();
    } catch (err) {
      console.warn(`Failed to close SSE stream for job ${jobId}`, err);
    }
  }, []);

  const applyJobEvent = useCallback(
    (payload: JobEventPayload) => {
      if (!payload?.job_id) {
        return;
      }
      setJobs((prev) => {
        let mutated = false;
        const nextJobs = prev.map((job) => {
          if (job.id !== payload.job_id) {
            return job;
          }
          const nextStage = payload.stage ?? payload.current_stage ?? job.current_stage ?? null;
          const nextMessage = payload.message ?? job.message ?? null;
          const nextProgress =
            typeof payload.progress_percent === 'number' ? payload.progress_percent : job.progress_percent;
          const nextStatus = payload.status ?? job.status;

          const updatedJob: JobResponse = {
            ...job,
            status: nextStatus,
            current_stage: nextStage,
            message: nextMessage,
            progress_percent: nextProgress ?? job.progress_percent,
            updated_at: payload.timestamp ?? job.updated_at,
          };
          mutated = true;
          return updatedJob;
        });
        return mutated ? nextJobs : prev;
      });

      if (
        (payload.status && TERMINAL_JOB_STATUSES.has(payload.status)) ||
        payload.event_type === 'terminal'
      ) {
        stopStream(payload.job_id);
      }
    },
    [stopStream],
  );

  const handleStreamMessage = useCallback(
    (raw: string) => {
      if (!raw) {
        return;
      }
      try {
        const payload = JSON.parse(raw) as JobEventPayload;
        if (payload?.job_id) {
          applyJobEvent(payload);
        }
      } catch (err) {
        console.error('Failed to parse job stream payload', err, raw);
      }
    },
    [applyJobEvent],
  );

  useEffect(() => {
    if (!apiService) {
      return;
    }
    const activeJobIds = new Set<string>();

    jobs.forEach((job) => {
      if (TERMINAL_JOB_STATUSES.has(job.status)) {
        stopStream(job.id);
        return;
      }

      activeJobIds.add(job.id);
      if (!streamControllers.current[job.id]) {
        const disposer = apiService.subscribeToSse(`/api/v1/jobs/${job.id}/events/stream`, {
          onMessage: handleStreamMessage,
          onError: (err) => {
            console.error(`Job ${job.id} stream error`, err);
            stopStream(job.id);
          },
          onClose: () => {
            delete streamControllers.current[job.id];
          },
        });
        streamControllers.current[job.id] = disposer;
      }
    });

    Object.keys(streamControllers.current).forEach((jobId) => {
      if (!activeJobIds.has(jobId)) {
        stopStream(jobId);
      }
    });
  }, [apiService, jobs, handleStreamMessage, stopStream]);

  useEffect(() => {
    return () => {
      Object.keys(streamControllers.current).forEach((jobId) => stopStream(jobId));
    };
  }, [stopStream]);

  const handleRetry = useCallback(async (jobId: string) => {
    if (!apiService) {
      return;
    }
    setActionError(null);
    setActionLoading((prev) => ({ ...prev, [jobId]: 'retry' }));
    try {
      const updated = (await apiService.post(`/api/v1/jobs/${jobId}/retry`)) as JobResponse;
      setJobs((prev) => prev.map((job) => (job.id === jobId ? updated : job)));
    } catch (err) {
      console.error('Failed to retry job', err);
      setActionError('We could not retry that task. Please try again in a moment.');
    } finally {
      setActionLoading((prev) => {
        const next = { ...prev };
        delete next[jobId];
        return next;
      });
    }
  }, [apiService]);

  const handleDismiss = useCallback(async (jobId: string) => {
    if (!apiService) {
      return;
    }
    setActionError(null);
    setActionLoading((prev) => ({ ...prev, [jobId]: 'dismiss' }));
    try {
      await apiService.delete(`/api/v1/jobs/${jobId}`);
      setJobs((prev) => prev.filter((job) => job.id !== jobId));
    } catch (err) {
      console.error('Failed to dismiss job', err);
      setActionError('We could not dismiss that task. Please try again in a moment.');
    } finally {
      setActionLoading((prev) => {
        const next = { ...prev };
        delete next[jobId];
        return next;
      });
    }
  }, [apiService]);

  const content = useMemo(() => {
    if (loading) {
      return (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
          <CircularProgress />
        </Box>
      );
    }

    if (error) {
      return (
        <Alert severity="error" sx={{ mt: 2 }}>
          {error}
        </Alert>
      );
    }

    if (jobs.length === 0) {
      return (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <Typography variant="h6" gutterBottom>
            No tasks yet
          </Typography>
          <Typography variant="body2" color="text.secondary">
            When you start downloads or longer running actions, they will appear here so you can keep track of
            progress.
          </Typography>
        </Paper>
      );
    }

    return (
      <Paper sx={{ overflowX: 'auto' }}>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell>Task</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Progress</TableCell>
              <TableCell>Last Update</TableCell>
              <TableCell>Details</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {jobs.map((job) => {
              const statusColor = STATUS_TO_COLOR[job.status] ?? 'default';
              const progress = Number.isFinite(job.progress_percent) ? job.progress_percent : undefined;
              const friendlyName = JOB_TYPE_LABELS[job.job_type] || job.job_type;
              const isTerminal = TERMINAL_JOB_STATUSES.has(job.status);
              const isRetryable = ['failed', 'canceled'].includes(job.status);
              const jobAction = actionLoading[job.id];
              return (
                <TableRow key={job.id} hover>
                  <TableCell>
                    <Typography variant="subtitle2">{friendlyName}</Typography>
                    {job.result?.model_name ? (
                      <Typography variant="body2" color="text.secondary">
                        {job.result.model_name as string}
                      </Typography>
                    ) : null}
                  </TableCell>
                  <TableCell>
                    <Chip label={job.status.replace(/_/g, ' ')} color={statusColor} size="small" />
                  </TableCell>
                  <TableCell sx={{ width: 220 }}>
                    {typeof progress === 'number' ? (
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <LinearProgress variant="determinate" value={Math.min(100, Math.max(0, progress))} sx={{ flex: 1 }} />
                        <Typography variant="body2" color="text.secondary" sx={{ minWidth: 36 }}>
                          {Math.round(progress)}%
                        </Typography>
                      </Box>
                    ) : (
                      <LinearProgress />
                    )}
                  </TableCell>
                  <TableCell>{formatDateTime(job.updated_at || job.created_at)}</TableCell>
                  <TableCell sx={{ maxWidth: 320 }}>
                    <Typography variant="body2" color="text.secondary">
                      {job.message || '—'}
                    </Typography>
                  </TableCell>
                  <TableCell align="right">
                    <Stack direction="row" spacing={1} justifyContent="flex-end">
                      {isRetryable ? (
                        <Button
                          variant="outlined"
                          size="small"
                          onClick={() => handleRetry(job.id)}
                          disabled={Boolean(jobAction)}
                        >
                          {jobAction === 'retry' ? 'Retrying…' : 'Retry'}
                        </Button>
                      ) : null}
                      {isTerminal ? (
                        <Button
                          variant="text"
                          size="small"
                          color="inherit"
                          onClick={() => handleDismiss(job.id)}
                          disabled={Boolean(jobAction)}
                        >
                          {jobAction === 'dismiss' ? 'Removing…' : 'Dismiss'}
                        </Button>
                      ) : null}
                    </Stack>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Paper>
    );
  }, [jobs, loading, error]);

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ xs: 'flex-start', sm: 'center' }} spacing={2} mb={3}>
        <Box>
          <Typography variant="h4" gutterBottom>
            My Tasks
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Keep tabs on downloads and longer running actions while you keep working.
          </Typography>
        </Box>
        <Button
          variant="outlined"
          startIcon={<RefreshIcon />}
          onClick={fetchJobs}
          disabled={refreshing}
        >
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </Button>
      </Stack>
      {actionError ? (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {actionError}
        </Alert>
      ) : null}
      {content}
    </Box>
  );
};

export default TasksPage;
