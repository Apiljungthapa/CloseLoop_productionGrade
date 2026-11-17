# Period Management & Automated Close Process - Implementation Guide

## Overview

This document describes the implementation of automated period management and the close process in CloseLoop. The system now automatically manages the period lifecycle, generating plans and handling work carry-forward between periods.

## Architecture

### Components

1. **APScheduler** - Background job scheduler
2. **Scheduler Service** - Monitors periods and triggers closure
3. **Notification Service** - Sends notifications to Team Leads
4. **Carry-Forward Service** - Manages incomplete work transfer (existing)
5. **Period Service** - Period creation and management (existing)

### Period Lifecycle

```
Current Period (OPEN)
    ↓
End Date Reached
    ↓
Scheduler Job Triggered
    ↓
Close Period (Existing Function)
    ├─ Mark current as CLOSED
    ├─ Create next OPEN period
    ├─ Generate Plans from Close Packs
    ├─ Build Carry-Forward Group
    └─ Log Audit Trail
    ↓
Send Notifications to Team Leads
    ↓
New Period OPEN with Plans Ready
```

## Scheduler Configuration

### Execution Schedule

The background scheduler runs TWO jobs to ensure reliable period closure:

1. **Daily Job**: Runs at **00:01 UTC** every day
   - Checks for all periods where `end_date <= today`
   - Automatically closes expired periods

2. **Hourly Job**: Runs at **:00 UTC** every hour
   - Safety check for periods nearing/past end time
   - Catches edge cases around timezone boundaries

Both jobs are **idempotent** - multiple executions won't create duplicate periods or duplicate plans.

### Logging

All scheduler activity is logged to:
- Application logs (see `services/scheduler_service.py`)
- Audit trail (database: `audit_logs` table)
  - Action: `PERIOD_CLOSED`
  - Metadata: next_period_id, plans_generated, carry_forward_items

## API Endpoints

### Manual Period Closure (Admin Only)

```
POST /api/periods/admin/trigger-scheduler
```

**Purpose**: Manually trigger the scheduler's period closure check. Useful for:
- Testing period closure in development
- Forcing immediate closure without waiting for scheduled job
- Debugging

**Response**:
```json
{
  "status": "success|error",
  "message": "Scheduler job executed. Check logs for details."
}
```

**Example**:
```bash
curl -X POST http://localhost:8000/api/periods/admin/trigger-scheduler \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json"
```

### Existing Period Close Endpoint

```
POST /api/periods/{period_id}/close
```

Manually close a specific period. Returns:
```json
{
  "closed_period": {...},
  "next_period": {...},
  "plans_generated": 12,
  "carry_forward_items": 3
}
```

### View Notifications

```
GET /api/notifications
```

Returns unread notifications for current user. Example response:
```json
[
  {
    "id": "...",
    "type": "PERIOD_TRANSITION",
    "message": "Period P006 01-Jun-2025 - 29-Jun-2025 has closed...",
    "link": "/periods/...",
    "read": false,
    "created_at": "2025-07-01T00:01:30Z"
  }
]
```

### Mark Notification as Read

```
POST /api/notifications/{notification_id}/read
```

## Period Configuration

Configure periods per tenant:

```
GET /api/periods/configuration
PUT /api/periods/configuration
```

Configuration schema:
```json
{
  "start_day_of_week": 0,           // 0=Monday, 4=Friday
  "duration_weeks": 4,               // 4-week periods
  "auto_generate": true,             // Auto-generate plans
  "carry_forward_enabled": true      // Carry forward incomplete items
}
```

## Database Schema Changes

No new tables were added. The system uses existing models:
- `Period` - Period tracking
- `PeriodConfiguration` - Tenant settings
- `Plan`, `Task`, `SubTask`, `Checkpoint` - Work items
- `CarryForwardGroup`, `CarryForwardItem` - Incomplete work tracking
- `Notification` - User notifications
- `AuditLog` - Action history

## Workflow Example

### Scenario: 4-Week Period Ending Friday June 28, 2025

**Thursday June 27, 2025 at 22:00 UTC:**
- Period still OPEN
- All team members working on tasks

**Friday June 28, 2025 at 00:01 UTC:**
- Scheduler job runs
- Period end_date (June 28) ≤ today (June 28) → Close triggered
- `close_period()` executes:
  1. Period P006 marked CLOSED
  2. Period P007 created as OPEN (June 29 - July 26)
  3. Plans regenerated from active Close Pack for all entities
  4. Incomplete tasks/subtasks added to CarryForwardGroup (status=PENDING)
  5. Audit log: `PERIOD_CLOSED` with metadata
- Notifications sent to Team Leads: "Period P006 has closed. Period P007 is open with 24 plans..."

**Friday June 28, 2025 at 09:00 UTC:**
- Team Leads view notifications
- Carry-forward group approval endpoint available for reviewers
- New period P007 dashboard shows generated plans

## Carry-Forward Process

### Automatic Carry-Forward

When period closes, incomplete items are identified:
```python
_OPEN_STATUSES = (
    TaskStatus.NOT_STARTED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.PENDING_REVIEW,
    TaskStatus.REJECTED,
)
```

Items with these statuses are copied to `CarryForwardItem` with:
- Reference to original item (for audit)
- Status snapshot at time of closure
- Original assignee
- Snapshot of task/subtask metadata

### Manual Approval Workflow

Via existing endpoint: `POST /api/carry-forwards/{group_id}/approve`

Decision options:
- `APPROVE` - Copy all items to new period as new Tasks/SubTasks
- `REJECT` - Keep items in CarryForwardGroup (no copy)

## Notifications

### Notification Types

1. **PERIOD_TRANSITION**
   - Sent to: Team Leads
   - When: Period closes, new one opens
   - Content: "Period X closed. Period Y open with N plans."

2. **PLAN_ASSIGNED** (hook point for future)
   - When: Plan created
   - Content: "Plan 'X' assigned to you"

3. **SUBTASK_PENDING_REVIEW**
   - When: Subtask submitted for review
   - Content: "Subtask 'X' pending your review"

4. **SUBTASK_APPROVED**
   - When: Subtask approved
   - Content: "Your subtask 'X' approved"

5. **SUBTASK_REJECTED**
   - When: Subtask rejected
   - Content: "Your subtask 'X' rejected"

### Accessing Notifications

Frontend can:
1. Poll: `GET /api/notifications` every N seconds
2. WebSocket: Subscribe to notifications via `/ws` endpoint
3. Mark read: `POST /api/notifications/{id}/read`

## Error Handling

### Period Not Found
- Already closed periods are skipped
- Gracefully handles missing periods

### Plan Generation Failure
- Logs error but continues
- Previous period still closes
- Next period still created
- Failed entities can be retried

### Notification Errors
- Period still closes successfully
- Notification errors logged but don't block closure
- Notifications can be resent manually

## Development & Testing

### Testing Period Closure

1. **Setup test period ending today:**
   ```bash
   # Via API - POST /api/periods/import
   # Set period end_date to today or yesterday
   ```

2. **Trigger scheduler manually:**
   ```bash
   POST /api/periods/admin/trigger-scheduler
   ```

3. **Verify:**
   - Check period status changed to CLOSED
   - Check new OPEN period created
   - Check plans generated in new period
   - Check notifications sent
   - Check audit logs recorded

### Logs to Monitor

```
docker logs closeloop-backend | grep -i "scheduler\|period\|auto-close"
```

Expected output:
```
INFO - Background scheduler initialized
INFO - Auto-closing period {id}
INFO - Scheduler job executed...
```

## Deployment Considerations

### Environment

- Python 3.9+
- APScheduler 3.11.1+ (added to requirements.txt)
- Timezone: Use UTC for all period end_date calculations
- No Redis required (uses in-memory scheduler)

### Startup

Scheduler initializes automatically when FastAPI starts. See `main.py` lifespan handler.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await initialize_scheduler()
    yield
    # Shutdown
    await shutdown_scheduler()
```

### Monitoring

Monitor logs for:
- `"Background scheduler initialized"` - Successful startup
- `"Auto-closing period"` - Period closure executed
- Any error messages starting with `ERROR in auto-close job`

If scheduler fails to initialize, app will log error but continue running (manual API calls still work).

## Future Enhancements

1. **Email Notifications** - Send email to Team Leads on closure
2. **WebSocket Notifications** - Real-time period updates
3. **Rollback Capability** - Manual period reopen if needed
4. **Custom Schedules** - Per-tenant scheduling rules
5. **Concurrent Period Support** - Multiple open periods (if business requires)
6. **Audit Reports** - Period closure audit trail export

## Troubleshooting

### Scheduler Not Running

**Check:**
1. App startup logs for scheduler initialization
2. Docker logs if containerized
3. System timezone is UTC or correctly offset

**Fix:**
1. Restart application
2. Check `backend/requirements.txt` has `apscheduler>=3.11.1`
3. Trigger manually: `POST /api/periods/admin/trigger-scheduler`

### Period Didn't Close Automatically

**Check:**
1. Period end_date is actually in the past
2. Period status is OPEN or IN_PROGRESS (not CLOSED)
3. At least one Close Pack exists and is active
4. Check logs for errors

**Fix:**
1. Verify period config: `GET /api/periods/configuration`
2. Manually trigger: `POST /api/periods/admin/trigger-scheduler`
3. Or close manually: `POST /api/periods/{id}/close`

### Duplicate Plans Generated

**Cause:** Scheduler job ran multiple times before previous job completed

**Prevention:** Already handled - jobs are idempotent

**Fix:**
1. Check for duplicate plans: `GET /api/plans?period_id={id}`
2. Delete duplicates via API or database
3. Restart scheduler: restart application

## References

- **APScheduler Docs**: https://apscheduler.readthedocs.io/
- **FastAPI Lifespan**: https://fastapi.tiangolo.com/advanced/events/
- **SQLAlchemy Async**: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html

