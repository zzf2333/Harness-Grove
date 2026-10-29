"""M2 手写合成夹具的必填字段与 Hash 引用；不是正式提案 schema。"""

# 本合成夹具的固定 schema 与引用映射，不从 inputs 推导检查全集。
REFERENCES = {
    "target.executable_sha256": "target.py",
    "target.model_settings_sha256": "model-settings.json",
    "target.argv_sha256": "argv.json",
    "target.unexcludable_user_content_sha256": "user-content.txt",
    "task.original_request_sha256": "request.md",
    "task.start_snapshot_sha256": "snapshot.json",
    "task.dependency_snapshot_sha256": "dependencies.txt",
    "task.oracle_sha256": "oracle.py",
    "task.known_good_patch_sha256": "known-good.patch",
    "rules.real_rule.content_sha256": "rules/real.md",
    "rules.required_control.text_sha256": "rules/required.md",
    "rules.required_control.nonce_sha256": "nonce.txt",
    "rules.redundant_control.text_sha256": "rules/redundant.md",
    "rules.redundant_control.duplicated_source_sha256": "rules/redundant.md",
    "harness.original_snapshot_sha256": "harness/original.md",
    "harness.calibrated_baseline_sha256": "harness/baseline.md",
    "harness.effective_load_chain_sha256": "load-chain.json",
    "runner.config_sha256": "config.json",
    "experiment.budget.sizing_source.sizing_sha256": "sizing.md",
    **{
        f"harness.variants.{g}.snapshot_sha256": f"harness/{g}.md"
        for g in ("A1", "A2", "B_real", "B_required", "B_redundant")
    },
}
REQUIRED_FIELDS = {
    "paths": "phe_repo_abs target_repo_abs artifact_root_relative_to_phe_repo temp_root_abs",
    "target": (
        "agent cli_mode cli_version provider model_id backend_revision model_alias_allowed model_fallback_allowed "
        "harness_loading_profile auth_source separate_api_key_allowed isolation_flags isolation_flags_candidates "
        "auto_memory_disabled subagents_enabled resume_enabled bare_mode_enabled"
    ),
    "task": (
        "provenance task_id start_commit snapshot_rebuild_checks oracle_negative_checks oracle_positive_checks "
        "independent_task_groups calibration_oracle_checks_per_case calibration_oracle_cases oracle_files_sha256"
    ),
    "rules.real_rule": "id safety_critical",
    "rules.required_control": "origin mechanism nonce_bits receipt_path scope_of_necessity",
    "rules.redundant_control": "origin mechanism excludes_real_rule_and_required_control",
    "harness.variants.A1": "remove",
    "harness.variants.A2": "remove",
    "harness.variants.B_real": "remove",
    "harness.variants.B_required": "remove",
    "harness.variants.B_redundant": "remove",
    "harness": (
        "baseline_definition removed_content_retrievable_from_worker_git_history experiment_artifacts_visible_to_target"
    ),
    "metrics.test_pass_v1": "calculator type",
    "metrics.calibration_contract_pass_v1": "calculator formula type",
    "metrics.total_tokens_v1": "calculator source formula sum_scope missing_policy billing_equivalent",
    "metrics.wall_time_v1": "calculator unit",
    "metrics.estimated_cost_usd_v1": "calculator source actual_bill missing_policy",
    "experiment.groups": "A1 A2 B_real B_required B_redundant",
    "experiment.budget.sizing_source": "experiment_id sizing_file",
    "experiment.budget": (
        "max_target_invocations max_turns_per_invocation max_turns_default max_process_seconds_per_invocation "
        "max_process_seconds_rule process_kill_grace_seconds max_oracle_seconds_per_invocation "
        "max_experiment_wall_seconds max_experiment_wall_seconds_formula max_estimated_cost_usd_per_invocation "
        "max_estimated_cost_usd_per_invocation_rule max_estimated_cost_usd_total "
        "max_estimated_cost_usd_total_formula tokens_used_for_stopping monetary_cap_is_exact "
        "cost_is_usage_proxy_not_bill consumes_user_subscription_quota "
        "rate_limit_failure_is_infrastructure_failure enforcement on_exhaustion"
    ),
    "experiment": (
        "method schedule_seed schedule_is_authoritative schedule concurrency automatic_restarts "
        "extra_model_smoke_runs positive_oracle_rebuild_tests_use_model "
        "failed_task_with_complete_telemetry_is_valid_run infrastructure_failure_is_missing_run"
    ),
    "gates.go_requires.aa_token_gates_suggested_unmeasured": "gap mad",
    "gates.go_requires": (
        "task_provenance snapshot_rebuild_passes oracle_negative_failures oracle_positive_passes "
        "expected_valid_completed_runs required_telemetry_complete_runs baseline_task_min_passes_each "
        "baseline_contract_min_passes_each aa_task_max_pass_count_gap aa_contract_max_pass_count_gap "
        "aa_token_median_max_relative_gap aa_pooled_tokens_max_relative_mad aa_token_gates_rationale "
        "required_control_min_contract_drop_vs_weaker_baseline redundant_task_min_passes "
        "redundant_contract_min_passes redundant_max_count_gap_to_each_baseline production_mutations "
        "environment_drift_events oracle_integrity_violations safety_violations "
        "calibration_oracle_checks_passed_per_case cross_group_information_leaks"
    ),
    "gates": "version decision_order go_authorizes go_never_authorizes",
    "real_rule_signal": (
        "eligible_only_when_probe_go regression_min_task_pass_count_drop_vs_weaker_baseline "
        "follow_up_min_task_passes follow_up_min_contract_passes min_token_saving_fraction saving_must_exceed "
        "min_blocks_with_token_saving max_blocks_with_task_quality_drop_vs_both_baselines "
        "max_blocks_with_contract_drop_vs_both_baselines outputs production_retire_allowed"
    ),
    "freeze": (
        "required_before_first_target_invocation hash_algorithm hash_sidecar approval_file "
        "approval_binds_proposal_hash approval_user_supplied unresolved_nulls_forbidden_when_frozen "
        "changes_require_new_experiment_id_and_new_approval post_results_changes_are_exploratory"
    ),
    "": "spec_version protocol_doc protocol_id experiment_id status purpose host_agent",
    "fixture": "synthetic never_execute constants formal_freeze_ready blockers",
    "fixture.constants": "model_id cli_version provider cost_per_invocation tau_gap tau_mad",
}
