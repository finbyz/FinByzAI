# Automation Engine Redis-Unavailable Drill

The Workflow Builder uses Redis for background job queuing (RQ). It relies on the transactional outbox (`Automation Outbox Event`) and token lease states (`Automation Run Token`) to ensure database-driven resilience when Redis is unavailable.

## Objective
Verify that if Redis crashes or is temporarily unreachable:
1. No workflows lose state.
2. Web requests continue to succeed (events are saved to the Outbox).
3. When Redis recovers, the system catches up without duplicate executions.

## Execution Steps

1. **Baseline Load Generation:**
   - Enable `Automation Settings` and start several runs of a test workflow (e.g., Lead automation).
   - Ensure workers are processing.

2. **Simulate Redis Failure:**
   - Stop the Redis service: `sudo systemctl stop redis-server` or `docker stop redis`.
   - The Frappe background workers will crash or enter a reconnect loop.

3. **Verify API Resilience:**
   - Trigger several document updates that should enqueue workflows.
   - **Expectation:** The Frappe API and frontend must remain responsive. The system must degrade gracefully. Events are committed to `Automation Outbox Event` with `status="PENDING"`.

4. **Verify Engine Safety:**
   - Check `Automation Run Token`. Some tokens may be stuck in `status="RUNNING"`.
   - Check `Automation Outbox Event`. Events should accumulate in `status="PENDING"`.

5. **Recovery:**
   - Start the Redis service: `sudo systemctl start redis-server` or `docker start redis`.
   - Ensure Frappe workers reconnect.

6. **Reconciliation & Catch-Up:**
   - The `check_queue_health` monitor will log warnings if the outage exceeded thresholds.
   - The scheduled dispatcher (`events.dispatch_outbox` and `engine.dispatch_ready_tokens`) will automatically pick up `PENDING` outbox events and `READY` tokens.
   - The lease-recovery sweep (`events._recover_expired_leases`) will find `RUNNING` tokens/events whose leases expired during the crash, mark them back to `READY`/`FAILED`, and the dispatcher will retry them.
   - **Expectation:** Within 5-10 minutes, all queues should drain, and no tasks should be lost.
