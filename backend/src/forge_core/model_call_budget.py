"""Durable, conservative admission for each provider HTTP attempt."""

import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import closing

from .provider_pool import PriceBound
from .runtime import ModelCallContext
from .token_policy import token_budgets_enforced


class BudgetExceeded(RuntimeError):
    pass


class ConcurrencyUnavailable(BudgetExceeded):
    pass


class ModelCallBudgetLedger:
    def __init__(
        self,
        path,
        *,
        token_limit,
        concurrency_limit,
        cost_limit=None,
        currency=None,
        price_bounds=None,
        scope_limits=None,
        scope_cost_reconciliations=None,
        isolation_seconds=60,
        reconciled_historical_cost=None,
        remote_max_seconds=None,
        enforce_token_budgets=None,
    ):
        if enforce_token_budgets is None:
            enforce_token_budgets = token_budgets_enforced()
        if type(enforce_token_budgets) is not bool:
            raise ValueError("Invalid token budget policy")
        if (
            (token_limit is not None and (type(token_limit) is not int or token_limit < 0))
            or (enforce_token_budgets and token_limit is None)
            or type(concurrency_limit) is not int
            or concurrency_limit < 1
        ):
            raise ValueError("Invalid budget limits")
        if cost_limit is not None and (
            type(cost_limit) is not int
            or cost_limit < 0
            or not isinstance(currency, str)
            or not re.fullmatch(r"[A-Z]{3}", currency)
        ):
            raise ValueError("Invalid cost limit")
        if (
            type(isolation_seconds) not in (int, float)
            or not math.isfinite(isolation_seconds)
            or isolation_seconds < 0
        ):
            raise ValueError("Invalid isolation period")
        self.path = str(path)
        self.enforce_token_budgets = enforce_token_budgets
        self.configured_token_limit = token_limit
        self.token_limit = token_limit if enforce_token_budgets else None
        self.concurrency_limit = concurrency_limit
        self.cost_limit = cost_limit
        self.currency = currency
        self.price_bounds = dict(price_bounds or {})
        if any(
            not isinstance(v, PriceBound) or k != (v.provider_id, v.model_id)
            for k, v in self.price_bounds.items()
        ):
            raise ValueError("Invalid operator price bounds")
        if reconciled_historical_cost is not None and (
            type(reconciled_historical_cost) is not int or reconciled_historical_cost < 0
        ):
            raise ValueError("Invalid reconciled historical cost")
        self.reconciled_historical_cost = reconciled_historical_cost
        self.scope_limits = dict(scope_limits or {})
        self.scope_cost_reconciliations = dict(scope_cost_reconciliations or {})
        if any(
            not isinstance(k, tuple)
            or len(k) != 3
            or k[0] not in {"quest", "team", "worker"}
            or not all(isinstance(x, str) and x for x in k)
            or not re.fullmatch(r"[A-Z]{3}", k[2])
            or type(v) is not int
            or v < 0
            for k, v in self.scope_cost_reconciliations.items()
        ):
            raise ValueError("Invalid scope cost reconciliation")
        if any(
            not isinstance(k, tuple)
            or len(k) != 2
            or k[0] not in {"workspace", "quest", "worker"}
            or not isinstance(k[1], str)
            or not isinstance(v, tuple)
            or len(v) != 2
            or type(v[0]) is not int
            or v[0] < 0
            or type(v[1]) is not int
            or v[1] < 1
            for k, v in self.scope_limits.items()
        ):
            raise ValueError("Invalid scope limits")
        self.isolation_seconds = isolation_seconds
        if remote_max_seconds is not None and (
            type(remote_max_seconds) not in (int, float) or not 0 < remote_max_seconds <= 86400
        ):
            raise ValueError("Invalid provider remote execution bound")
        self.remote_max_seconds = remote_max_seconds

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def cooldown_remaining(self, provider_id):
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT retry_after FROM model_provider_cooldowns WHERE provider_id=?",
                (provider_id,),
            ).fetchone()
        return max(0.0, row[0] - time.time()) if row else 0.0

    def defer_provider(self, provider_id, seconds):
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Invalid retry delay")
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO model_provider_cooldowns VALUES (?,?) ON CONFLICT(provider_id) DO UPDATE SET retry_after=MAX(retry_after,excluded.retry_after)",
                (provider_id, time.time() + seconds),
            )
            conn.commit()

    def reject_completed(self, attempt_id):
        """A fully received rejection releases concurrency, never the cost/token reserve."""
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE model_budget_attempts SET state='rejected' WHERE attempt_id=? AND state='reserved'",
                (attempt_id,),
            )
            conn.commit()

    def admit(self, context, provider_id, model_id, *, input_tokens, output_tokens, remote_timeout):
        if not isinstance(context, ModelCallContext) or any(
            type(v) is not int or v < 0 for v in (input_tokens, output_tokens)
        ):
            raise ValueError("Invalid model reservation")
        if type(remote_timeout) not in (int, float) or not 0 < remote_timeout <= 600:
            raise ValueError("Invalid remote timeout")
        bound = self.price_bounds.get((provider_id, model_id))
        cost = None
        if self.cost_limit is not None and (
            self.reconciled_historical_cost is None
            or bound is None
            or bound.currency != self.currency
        ):
            raise BudgetExceeded("Historical cost or operator price bound unknown")
        if bound is not None:
            cost = (
                input_tokens * bound.input_microunits_per_million
                + output_tokens * bound.output_microunits_per_million
                + 999999
            ) // 1000000
        now = time.time()
        attempt_id = uuid.uuid4().hex
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if context.purpose == "independent_reviewer":
                # Fence a delayed reviewer before any HTTP admission. Claim release
                # and this check share SQLite's write lock, so a refunded claim
                # cannot later consume another owner's bounded review allowance.
                claim = conn.execute(
                    "SELECT payload FROM events WHERE event_type='review.started' "
                    "AND json_extract(payload,'$.run_id')=? ORDER BY seq DESC LIMIT 1",
                    (context.worker_run_id,),
                ).fetchone()
                result = conn.execute(
                    "SELECT id FROM events WHERE event_type='worker.result' "
                    "AND json_extract(payload,'$.run_id')=? ORDER BY seq DESC LIMIT 1",
                    (context.worker_run_id,),
                ).fetchone()
                started = json.loads(claim[0]) if claim else {}
                released = conn.execute(
                    "SELECT 1 FROM events WHERE event_type='review.released' "
                    "AND json_extract(payload,'$.call_id')=? "
                    "AND json_extract(payload,'$.reservation_refunded')=1",
                    (context.call_id,),
                ).fetchone()
                authorized = conn.execute(
                    "SELECT 1 FROM worker_runs r JOIN quests q ON q.id=r.quest_id "
                    "JOIN quest_plans p ON p.id=r.plan_id WHERE r.id=? "
                    "AND r.state='review_pending' AND q.status='running' "
                    "AND q.plan_revision=r.plan_revision AND p.revision=r.plan_revision "
                    "AND p.status='approved' AND p.digest=r.plan_digest",
                    (context.worker_run_id,),
                ).fetchone()
                if (
                    not context.worker_run_id
                    or authorized is None
                    or started.get("call_id") != context.call_id
                    or result is None
                    or started.get("result_event_id") != result[0]
                    or released
                ):
                    raise BudgetExceeded("Independent review claim is stale or refunded")
            if conn.execute(
                "SELECT breached FROM model_budget_metadata WHERE singleton=1"
            ).fetchone()[0]:
                raise BudgetExceeded("Provider usage exceeded reservation; ledger is fenced")
            cooldown = conn.execute(
                "SELECT retry_after FROM model_provider_cooldowns WHERE provider_id=?",
                (provider_id,),
            ).fetchone()
            if cooldown is not None and cooldown[0] > now:
                raise ConcurrencyUnavailable("Provider cooldown active")
            if self.cost_limit is not None:
                historical = conn.execute(
                    "SELECT reconciled_cost,reconciled_currency FROM model_budget_metadata WHERE singleton=1"
                ).fetchone()
                if historical["reconciled_cost"] is None:
                    conn.execute(
                        "UPDATE model_budget_metadata SET reconciled_cost=?,reconciled_currency=? WHERE singleton=1",
                        (self.reconciled_historical_cost, self.currency),
                    )
                elif (historical["reconciled_cost"], historical["reconciled_currency"]) != (
                    self.reconciled_historical_cost,
                    self.currency,
                ):
                    raise BudgetExceeded("Historical reconciliation does not match ledger")
            scopes = [("1=1", (), self.token_limit, self.concurrency_limit)]
            for name, value in (
                ("workspace", context.workspace_id),
                ("quest", context.quest_id),
                ("worker", context.worker_run_id),
            ):
                if value is not None:
                    limits = self.scope_limits.get(
                        (name, value), (self.token_limit, self.concurrency_limit)
                    )
                    if limits is not None:
                        column = {
                            "workspace": "workspace_id",
                            "quest": "quest_id",
                            "worker": "worker_run_id",
                        }[name]
                        scopes.append((f"{column}=?", (value,), limits[0], limits[1]))
            if context.quest_id is not None and context.worker_run_id is None:
                tables = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quest_plans'"
                ).fetchone()
                plan = (
                    conn.execute(
                        "SELECT p.graph_json FROM quest_plans p JOIN quests q ON q.id=p.quest_id AND q.plan_revision=p.revision WHERE p.quest_id=? AND p.status='approved' LIMIT 1",
                        (context.quest_id,),
                    ).fetchone()
                    if tables
                    else None
                )
                if plan is not None:
                    graph = json.loads(plan["graph_json"])
                    quest_token_limit = graph.get("max_total_tokens")
                    if type(quest_token_limit) is not int or quest_token_limit < 0:
                        raise BudgetExceeded("Invalid approved Quest token limit")
                    scopes.append(
                        (
                            "quest_id=?",
                            (context.quest_id,),
                            quest_token_limit,
                            self.concurrency_limit,
                        )
                    )
                    cap, currency = graph.get("max_cost_microunits"), graph.get("cost_currency")
                    if cap is not None:
                        if bound is None or bound.currency != currency:
                            raise BudgetExceeded("Quest price bound unknown")
                        legacy = conn.execute(
                            "SELECT 1 FROM model_budget_legacy_quests WHERE quest_id=?",
                            (context.quest_id,),
                        ).fetchone()
                        key = ("quest", context.quest_id, currency)
                        saved = conn.execute(
                            "SELECT cost FROM model_budget_scope_reconciliations WHERE scope=? AND scope_id=? AND currency=?",
                            key,
                        ).fetchone()
                        reconciliation = self.scope_cost_reconciliations.get(key)
                        opening = conn.execute(
                            "SELECT COALESCE(SUM(CASE WHEN currency=? THEN cost ELSE 0 END),0), COALESCE(SUM(CASE WHEN tokens>0 AND (cost IS NULL OR currency!=? OR currency IS NULL) THEN 1 ELSE 0 END),0) FROM model_budget_opening WHERE quest_id=?",
                            (currency, currency, context.quest_id),
                        ).fetchone()
                        historical = opening[0]
                        if legacy or opening[1]:
                            if (
                                reconciliation is None
                                or reconciliation < historical
                                or (saved is not None and saved[0] != reconciliation)
                            ):
                                raise BudgetExceeded("Quest historical cost unknown")
                            if saved is None:
                                conn.execute(
                                    "INSERT INTO model_budget_scope_reconciliations VALUES (?,?,?,?)",
                                    (*key, reconciliation),
                                )
                            historical = reconciliation
                        used = conn.execute(
                            "SELECT COALESCE(SUM(CASE WHEN currency=? THEN COALESCE(cost_charged,cost_reserved) ELSE 0 END),0), COALESCE(SUM(CASE WHEN cost_reserved IS NULL OR currency!=? OR currency IS NULL THEN 1 ELSE 0 END),0) FROM model_budget_attempts WHERE quest_id=?",
                            (currency, currency, context.quest_id),
                        ).fetchone()
                        if type(cap) is not int or used[1] or historical + used[0] + cost > cap:
                            raise BudgetExceeded("Approved Quest cost exhausted or unknown")
            if context.worker_run_id is not None:
                worker = conn.execute(
                    "SELECT quest_id,provider_id,model_id,token_limit,team_digest,cost_limit_microunits,currency FROM worker_runs WHERE id=?",
                    (context.worker_run_id,),
                ).fetchone()
                if worker is None or (
                    worker["quest_id"],
                    worker["provider_id"],
                    worker["model_id"],
                ) != (context.quest_id, provider_id, model_id):
                    raise BudgetExceeded("Invalid Worker budget context")
                workspace = conn.execute("SELECT id FROM workspace WHERE singleton=1").fetchone()
                if workspace is None or workspace["id"] != context.workspace_id:
                    raise BudgetExceeded("Invalid Worker workspace")
                mapping = conn.execute(
                    "SELECT team_digest FROM model_budget_worker_teams WHERE worker_run_id=?",
                    (context.worker_run_id,),
                ).fetchone()
                if mapping is not None and mapping["team_digest"] != worker["team_digest"]:
                    raise BudgetExceeded("Worker team budget binding changed")
                conn.execute(
                    "INSERT OR IGNORE INTO model_budget_worker_teams VALUES (?,?)",
                    (context.worker_run_id, worker["team_digest"]),
                )
                if worker["cost_limit_microunits"] is not None:
                    if bound is None or bound.currency != worker["currency"]:
                        raise BudgetExceeded("Worker price bound unknown")
                    currency = worker["currency"]
                    quest_cost = conn.execute(
                        "SELECT limit_microunits FROM worker_cost_ledger WHERE scope='quest' AND scope_id=? AND currency=?",
                        (context.quest_id, currency),
                    ).fetchone()
                    if quest_cost is None:
                        raise BudgetExceeded("Approved Quest cost limit missing")
                    team = conn.execute(
                        "SELECT body_json FROM team_proposals WHERE quest_id=? AND proposal_digest=?",
                        (context.quest_id, worker["team_digest"]),
                    ).fetchone()
                    team_limit = worker["cost_limit_microunits"]
                    if team is not None:
                        team_limit = json.loads(team["body_json"])["limits"]["max_cost_microunits"]
                    cost_scopes = [
                        (
                            "worker",
                            context.worker_run_id,
                            "worker_run_id=?",
                            worker["cost_limit_microunits"],
                        ),
                        (
                            "team",
                            worker["team_digest"],
                            "worker_run_id IN (SELECT worker_run_id FROM model_budget_worker_teams WHERE team_digest=?)",
                            team_limit,
                        ),
                        ("quest", context.quest_id, "quest_id=?", quest_cost["limit_microunits"]),
                    ]
                    for scope, scope_id, predicate, cap in cost_scopes:
                        base = conn.execute(
                            f"SELECT COALESCE(SUM(CASE WHEN currency=? THEN cost ELSE 0 END),0), COALESCE(SUM(CASE WHEN tokens>0 AND (cost IS NULL OR currency!=? OR currency IS NULL) THEN 1 ELSE 0 END),0) FROM model_budget_opening WHERE {predicate}",
                            (currency, currency, scope_id),
                        ).fetchone()
                        historical_cost = base[0]
                        if base[1] or (
                            scope == "quest"
                            and conn.execute(
                                "SELECT 1 FROM model_budget_legacy_quests WHERE quest_id=?",
                                (scope_id,),
                            ).fetchone()
                        ):
                            key = (scope, scope_id, currency)
                            reconciled = self.scope_cost_reconciliations.get(key)
                            saved = conn.execute(
                                "SELECT cost FROM model_budget_scope_reconciliations WHERE scope=? AND scope_id=? AND currency=?",
                                key,
                            ).fetchone()
                            if (
                                reconciled is None
                                or (saved is not None and saved[0] != reconciled)
                                or reconciled < historical_cost
                            ):
                                raise BudgetExceeded("Quest historical cost unknown")
                            if saved is None:
                                conn.execute(
                                    "INSERT INTO model_budget_scope_reconciliations VALUES (?,?,?,?)",
                                    (*key, reconciled),
                                )
                            historical_cost = reconciled
                        used = conn.execute(
                            f"SELECT COALESCE(SUM(CASE WHEN currency=? THEN COALESCE(cost_charged,cost_reserved) ELSE 0 END),0),COALESCE(SUM(CASE WHEN cost_reserved IS NULL OR currency!=? OR currency IS NULL THEN 1 ELSE 0 END),0) FROM model_budget_attempts WHERE {predicate}",
                            (currency, currency, scope_id),
                        ).fetchone()
                        if (
                            type(cap) is not int
                            or used[1]
                            or historical_cost + used[0] + cost > cap
                        ):
                            raise BudgetExceeded("Approved scope cost exhausted or unknown")
                scopes.append(
                    (
                        "worker_run_id=?",
                        (context.worker_run_id,),
                        worker["token_limit"],
                        self.concurrency_limit,
                    )
                )
                for scope, scope_id, predicate in (
                    ("quest", context.quest_id, "quest_id=?"),
                    (
                        "team",
                        worker["team_digest"],
                        "worker_run_id IN (SELECT worker_run_id FROM model_budget_worker_teams WHERE team_digest=?)",
                    ),
                ):
                    limit = conn.execute(
                        "SELECT token_limit FROM worker_budget_ledger WHERE scope=? AND scope_id=?",
                        (scope, scope_id),
                    ).fetchone()
                    if limit is None:
                        raise BudgetExceeded("Approved Worker allocation missing")
                    scopes.append(
                        (predicate, (scope_id,), limit["token_limit"], self.concurrency_limit)
                    )
            for predicate, parameters, token_limit, concurrency_limit in scopes:
                aggregate = conn.execute(
                    f"SELECT COALESCE(SUM(COALESCE(input_charged,input_reserved)+COALESCE(output_charged,output_reserved)),0) AS charged, COALESCE(SUM(CASE WHEN state='reserved' AND (recover_after IS NULL OR recover_after>?) THEN 1 ELSE 0 END),0) AS active FROM model_budget_attempts WHERE {predicate}",
                    (now, *parameters),
                ).fetchone()
                opening = conn.execute(
                    f"SELECT COALESCE(SUM(tokens),0) FROM model_budget_opening WHERE {predicate}",
                    parameters,
                ).fetchone()[0]
                if (
                    self.enforce_token_budgets
                    and aggregate["charged"] + opening + input_tokens + output_tokens > token_limit
                ):
                    raise BudgetExceeded("Model budget exhausted")
                if aggregate["active"] >= concurrency_limit:
                    raise ConcurrencyUnavailable("Model concurrency unavailable")
            if self.cost_limit is not None:
                known_opening = conn.execute(
                    "SELECT COALESCE(SUM(cost),0) FROM model_budget_opening WHERE currency=?",
                    (self.currency,),
                ).fetchone()[0]
                if self.reconciled_historical_cost < known_opening:
                    raise BudgetExceeded("Reconciled cost is below known opening")
                cost_aggregate = conn.execute(
                    "SELECT COALESCE(SUM(CASE WHEN currency=? THEN COALESCE(cost_charged,cost_reserved) ELSE 0 END),0), COALESCE(SUM(CASE WHEN cost_reserved IS NULL OR currency!=? OR currency IS NULL THEN 1 ELSE 0 END),0) FROM model_budget_attempts",
                    (self.currency, self.currency),
                ).fetchone()
                charged_cost = self.reconciled_historical_cost + cost_aggregate[0]
                if cost_aggregate[1] or charged_cost + cost > self.cost_limit:
                    raise BudgetExceeded("Model cost budget exhausted or unknown")
            conn.execute(
                "INSERT INTO model_budget_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    attempt_id,
                    context.call_id,
                    context.purpose,
                    context.workspace_id,
                    context.quest_id,
                    context.worker_run_id,
                    context.parent_call_id,
                    provider_id,
                    model_id,
                    input_tokens,
                    output_tokens,
                    cost,
                    getattr(bound, "currency", None),
                    "reserved",
                    now,
                    None
                    if self.remote_max_seconds is None
                    else now
                    + max(remote_timeout, self.remote_max_seconds)
                    + self.isolation_seconds,
                    None,
                    None,
                    None,
                    getattr(bound, "input_microunits_per_million", None),
                    getattr(bound, "output_microunits_per_million", None),
                    getattr(bound, "version", None),
                ),
            )
            conn.commit()
        return attempt_id

    def settle(self, attempt_id, usage):
        inp, out = usage.get("input_tokens"), usage.get("output_tokens")
        if any(type(v) is not int or v < 0 for v in (inp, out)):
            return
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM model_budget_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if row is None or row["state"] != "reserved":
                return
            overrun = inp > row["input_reserved"] or out > row["output_reserved"]
            if overrun:
                inp = max(inp, row["input_reserved"])
                out = max(out, row["output_reserved"])
            cost = (
                None
                if row["input_price"] is None or row["output_price"] is None
                else (inp * row["input_price"] + out * row["output_price"] + 999999) // 1000000
            )
            conn.execute(
                "UPDATE model_budget_attempts SET state=?,input_charged=?,output_charged=?,cost_charged=? WHERE attempt_id=?",
                ("overrun" if overrun else "settled", inp, out, cost, attempt_id),
            )
            if overrun:
                conn.execute("UPDATE model_budget_metadata SET breached=1 WHERE singleton=1")
            conn.commit()
