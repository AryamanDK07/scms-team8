# Test cases

Smart Complaint Management System — Team 8

365 test cases in 40 test classes, generated from the suite
itself rather than maintained by hand, so this list cannot drift out
of step with the code. Regenerate with:

```
cd src
python manage.py test complaints -v 2
```

A test marked *skipped on SQLite* is one of the SCMS-SR-006
database-trigger tests. Those need PostgreSQL and run under CI,
where all 365 pass and none skip.

## AccountLockoutTests (14)

- `test_a_wrong_password_still_says_so`
- `test_attempts_during_a_lock_are_recorded`
- `test_attempts_during_a_lock_do_not_extend_it`
- `test_blocked_attempt_says_why`
- `test_correct_password_is_refused_while_locked`
- `test_five_failures_lock_the_account`
- `test_four_failures_do_not_lock`
- `test_locking_records_a_security_event`
- `test_lockout_is_per_account`
- `test_login_succeeds_once_the_lock_expires`
- `test_only_one_lock_event_per_lock`
- `test_security_events_cannot_be_altered_or_deleted`
- `test_successful_login_clears_the_failure_count`
- `test_unknown_email_records_no_event`

## AssignmentTests (18)

- `test_admin_of_another_department_cannot_assign`
- `test_agent_cannot_assign`
- `test_agent_from_another_department_is_not_offered`
- `test_assigning_to_the_current_owner_is_rejected`
- `test_assignment_records_the_source_ip`
- `test_complainant_cannot_assign`
- `test_department_admin_can_assign`
- `test_eligible_agents_are_scoped_to_the_department`
- `test_first_assignment_moves_status_to_assigned`
- `test_first_assignment_needs_no_reason`
- `test_inactive_agent_is_not_eligible`
- `test_reason_appears_in_the_audit_trail`
- `test_reassignment_does_not_rewind_work_in_progress`
- `test_reassignment_without_a_reason_is_blocked`
- `test_unrouted_complaint_has_no_eligible_agents`
- `test_view_accepts_reassignment_with_a_reason`
- `test_view_blocks_reassignment_with_no_reason`
- `test_whitespace_does_not_count_as_a_reason`

## AttachmentTests (17)

- `test_a_file_at_exactly_five_megabytes_is_accepted`
- `test_a_file_over_five_megabytes_is_rejected`
- `test_a_fourth_file_is_rejected`
- `test_a_png_with_a_jpg_extension_is_rejected`
- `test_an_empty_file_is_rejected`
- `test_an_executable_is_rejected`
- `test_an_executable_renamed_to_jpg_is_still_rejected`
- `test_another_complainant_cannot_attach_to_this_complaint`
- `test_another_complainant_cannot_download_it`
- `test_content_type_is_taken_from_the_file_not_the_client`
- `test_jpg_is_accepted`
- `test_owner_can_download_their_evidence`
- `test_pdf_is_accepted`
- `test_png_is_accepted`
- `test_stored_path_does_not_reuse_the_uploaded_name`
- `test_three_files_are_accepted`
- `test_two_files_of_the_same_name_do_not_collide`

## AuditTrailCompletenessTests (11)

- `test_a_full_lifecycle_leaves_an_unbroken_trail`
- `test_an_audited_row_cannot_be_deleted`
- `test_an_audited_row_cannot_be_edited_afterwards`
- `test_assignment_records_the_owner_change`
- `test_lodging_is_audited`
- `test_no_flow_writes_a_row_without_an_actor`
- `test_priority_override_records_both_values`
- `test_reassignment_records_both_owners`
- `test_resolution_records_the_status_move`
- `test_status_change_records_both_statuses`
- `test_the_trail_survives_a_queryset_delete_attempt`

## AuditTrailTests (2)

- `test_audit_row_cannot_be_deleted`
- `test_audit_row_cannot_be_updated`

## CommentTests (15)

- `test_agent_from_another_department_cannot_post`
- `test_anonymous_is_sent_to_login`
- `test_another_complainant_cannot_post`
- `test_another_complainant_cannot_read_the_thread`
- `test_assigned_agent_can_comment`
- `test_body_is_stored_stripped`
- `test_both_parties_see_each_others_comments`
- `test_complainant_can_comment`
- `test_empty_comment_is_refused`
- `test_overlong_comment_is_refused`
- `test_reply_attaches_to_its_parent`
- `test_reply_cannot_cross_complaints`
- `test_reply_to_a_reply_is_refused`
- `test_service_refuses_a_parent_from_another_complaint`
- `test_thread_is_oldest_first_with_replies_grouped`

## DashboardTests (10)

- `test_a_breached_sla_is_counted_only_while_open`
- `test_a_complainant_is_refused_the_page`
- `test_a_department_admin_sees_their_department`
- `test_a_system_admin_sees_everything`
- `test_an_agent_sees_only_their_own_complaints`
- `test_counts_are_grouped_by_status_and_priority`
- `test_the_average_is_scoped_like_everything_else`
- `test_the_average_rating_appears_once_a_rating_exists`
- `test_the_other_departments_complaint_is_not_named`
- `test_the_page_renders_for_staff`

## DatabaseLevelAuditGuardTests (7)

- `test_complaint_audit_row_cannot_be_deleted_by_raw_sql` — *skipped on SQLite*
- `test_complaint_audit_row_cannot_be_updated_by_raw_sql` — *skipped on SQLite*
- `test_ordinary_tables_are_still_writable`
- `test_rows_can_still_be_appended`
- `test_security_event_cannot_be_deleted_by_raw_sql` — *skipped on SQLite*
- `test_security_event_cannot_be_updated_by_raw_sql` — *skipped on SQLite*
- `test_whole_complaint_trail_cannot_be_wiped`

## DeactivationTests (5)

- `test_a_deactivated_account_cannot_log_in`
- `test_a_deactivated_agent_is_not_offered_for_new_work`
- `test_an_active_account_can_log_in`
- `test_roles_are_editable_from_the_admin`
- `test_their_open_complaints_are_still_reassignable`

## DockerImageTests (6)

- `test_postgres_is_the_version_ci_tests_against`
- `test_the_application_waits_for_a_healthy_database`
- `test_the_container_does_not_run_as_root`
- `test_the_image_does_not_serve_with_the_development_server`
- `test_the_python_version_matches_what_the_sad_commits_to`
- `test_uploaded_evidence_survives_the_container`

## DraftTests (10)

- `test_a_draft_can_be_discarded`
- `test_a_draft_is_gone_after_seven_days`
- `test_a_draft_is_invisible_to_every_complaint_query`
- `test_a_draft_is_retrievable_before_seven_days`
- `test_a_draft_is_saved_without_passing_validation`
- `test_saving_again_replaces_the_draft`
- `test_submitting_the_complaint_clears_the_draft`
- `test_the_draft_is_filled_back_into_the_form`
- `test_the_purge_command_runs`
- `test_the_window_runs_from_the_last_save`

## DuplicateDetectionTests (12)

- `test_a_complainant_cannot_judge_a_flag`
- `test_a_different_category_is_not_a_duplicate`
- `test_a_flag_cannot_be_judged_twice`
- `test_a_near_identical_complaint_is_flagged`
- `test_an_agent_can_confirm_a_flag`
- `test_an_agent_can_dismiss_a_flag`
- `test_an_older_complaint_falls_outside_the_window`
- `test_an_unrelated_complaint_is_not_flagged`
- `test_another_complainants_identical_text_is_not_a_duplicate`
- `test_case_and_spacing_do_not_defeat_the_match`
- `test_flagging_twice_does_not_duplicate_the_flag`
- `test_nothing_is_closed_or_merged_by_a_flag`

## EmailLoginTests (8)

- `test_deactivated_account_cannot_log_in`
- `test_duplicate_email_refuses_both_accounts`
- `test_email_is_case_insensitive`
- `test_surrounding_whitespace_is_ignored`
- `test_unknown_email_is_refused`
- `test_username_is_not_accepted_in_place_of_email`
- `test_valid_email_and_password_creates_session`
- `test_wrong_password_is_refused`

## ExportTests (7)

- `test_a_complainant_cannot_export_at_all`
- `test_both_formats_report_the_same_rows`
- `test_csv_has_a_header_and_one_row_per_complaint`
- `test_csv_is_served_as_a_download`
- `test_pdf_is_a_pdf_and_is_not_empty`
- `test_the_export_obeys_role_scoping`
- `test_the_export_obeys_the_same_filter_as_the_page`

## LodgeComplaintTests (11)

- `test_category_is_mandatory`
- `test_description_at_exactly_two_thousand_is_accepted`
- `test_description_is_mandatory`
- `test_description_over_two_thousand_characters_is_refused`
- `test_inactive_sub_category_is_not_offered`
- `test_location_is_mandatory`
- `test_several_invalid_fields_each_report_their_own_error`
- `test_sub_category_from_another_category_is_refused`
- `test_sub_category_is_mandatory`
- `test_subject_is_mandatory`
- `test_valid_submission_is_saved_with_every_field`

## MasterDataTests (12)

- `test_a_deactivated_category_disappears_from_the_form`
- `test_a_deactivated_sub_category_disappears`
- `test_a_new_category_is_immediately_selectable`
- `test_an_unused_department_can_be_deactivated`
- `test_changing_the_matrix_changes_the_next_priority`
- `test_changing_the_sla_changes_the_next_deadline`
- `test_deactivating_a_category_leaves_existing_complaints_alone`
- `test_department_can_be_deactivated_once_nothing_routes_to_it`
- `test_department_in_use_cannot_be_deactivated`
- `test_master_data_screens_are_reachable`
- `test_sub_category_of_a_dead_category_disappears`
- `test_the_refusal_names_the_blocking_category`

## ObjectLevelAccessTests (9)

- `test_agent_can_work_assigned_complaint`
- `test_agent_from_another_department_cannot_view`
- `test_assigned_agent_can_view`
- `test_complainant_cannot_work_own_complaint`
- `test_department_admin_sees_own_department_only`
- `test_other_complainant_cannot_view`
- `test_owner_can_view`
- `test_system_admin_sees_everything`
- `test_user_without_profile_is_denied`

## PasswordHashingTests (8)

- `test_account_hashed_before_the_change_still_authenticates`
- `test_configured_hasher_is_argon2`
- `test_legacy_hash_is_upgraded_on_a_correct_login`
- `test_new_password_is_stored_as_argon2id`
- `test_password_is_not_echoed_back_in_the_response`
- `test_password_is_not_written_to_any_log`
- `test_raw_password_does_not_appear_in_the_record`
- `test_wrong_password_does_not_upgrade_the_hash`

## PasswordResetTests (10)

- `test_link_cannot_be_used_twice`
- `test_link_is_refused_after_fifteen_minutes`
- `test_link_lets_the_user_set_a_new_password`
- `test_link_still_works_inside_the_window`
- `test_link_validity_window_is_fifteen_minutes`
- `test_new_password_works_at_login`
- `test_request_sends_one_email_with_a_link`
- `test_second_link_cannot_undo_the_first_reset`
- `test_tampered_token_is_refused`
- `test_unknown_address_sends_nothing_but_does_not_say_so`

## PriorityMatrixTests (14)

- `test_a_different_category_gives_a_different_priority`
- `test_admin_can_override_through_the_view`
- `test_agent_cannot_override_priority`
- `test_category_without_a_priority_falls_back_to_medium`
- `test_changing_the_matrix_changes_later_complaints`
- `test_complainant_cannot_choose_their_own_priority`
- `test_complainant_cannot_override_priority`
- `test_override_does_not_change_the_status`
- `test_override_records_old_and_new_values`
- `test_override_to_the_same_priority_is_refused`
- `test_override_without_a_reason_is_blocked`
- `test_priority_comes_from_the_category`
- `test_priority_is_not_a_field_on_the_lodging_form`
- `test_view_blocks_an_override_with_no_reason`

## ProfileTests (2)

- `test_new_user_defaults_to_complainant`
- `test_role_and_department_persist`

## RateLimitTests (6)

- `test_the_counter_expires_with_its_window`
- `test_the_eleventh_login_attempt_is_refused`
- `test_the_limit_is_per_account`
- `test_the_login_limit_does_not_block_a_fresh_address`
- `test_the_refusal_says_why`
- `test_the_sixth_submission_in_an_hour_is_refused`

## RatingTests (14)

- `test_a_complaint_cannot_be_rated_twice`
- `test_a_department_with_no_ratings_averages_none_not_zero`
- `test_a_non_integer_score_is_refused`
- `test_a_rating_cannot_be_changed_once_given`
- `test_a_resolved_complaint_cannot_be_rated_yet`
- `test_a_score_outside_one_to_five_is_refused`
- `test_an_open_complaint_cannot_be_rated`
- `test_staff_cannot_rate_the_complaint`
- `test_the_average_counts_only_its_own_department`
- `test_the_comment_is_optional`
- `test_the_complainant_can_rate_a_closed_complaint`
- `test_the_department_average_is_the_mean_of_its_ratings`
- `test_the_form_appears_only_once_the_complaint_is_closed`
- `test_the_given_rating_replaces_the_form`

## ReferenceIdTests (3)

- `test_reference_id_carries_current_year`
- `test_reference_id_matches_required_format`
- `test_reference_ids_are_sequential_and_unique`

## ReopenWindowFlowTests (16)

- `test_a_complaint_can_only_be_reopened_once`
- `test_auto_closure_closes_it_on_day_eight`
- `test_auto_closure_ignores_complaints_that_are_not_resolved`
- `test_auto_closure_is_safe_to_run_twice`
- `test_auto_closure_leaves_complaints_inside_the_window`
- `test_auto_closure_records_no_actor`
- `test_only_a_resolved_complaint_can_be_reopened`
- `test_reopening_is_recorded_with_actor_and_reason`
- `test_reopening_is_refused_on_day_eight`
- `test_reopening_works_on_day_six`
- `test_resolved_at_survives_a_reopen`
- `test_staff_cannot_reopen_on_the_complainants_behalf`
- `test_the_button_appears_on_day_six_and_not_on_day_eight`
- `test_the_dry_run_changes_nothing`
- `test_the_management_command_closes_and_reports`
- `test_the_window_is_open_right_up_to_seven_days`

## ReopenWindowTests (4)

- `test_can_reopen_on_day_six`
- `test_cannot_reopen_an_open_complaint`
- `test_cannot_reopen_on_day_eight`
- `test_cannot_reopen_twice`

## ResolutionTests (15)

- `test_an_executable_as_proof_is_refused`
- `test_an_open_complaint_cannot_be_resolved`
- `test_complainant_cannot_resolve_their_own_complaint`
- `test_generic_status_change_still_cannot_reach_resolved`
- `test_no_proof_is_refused`
- `test_no_remarks_is_refused`
- `test_proof_does_not_consume_the_complainant_evidence_limit`
- `test_proof_is_stored_as_proof_not_evidence`
- `test_remarks_and_proof_resolve_the_complaint`
- `test_remarks_at_exactly_the_limit_are_accepted`
- `test_remarks_just_under_the_limit_are_refused`
- `test_resolution_is_recorded_in_the_audit_trail`
- `test_service_refuses_resolution_from_a_wrong_status`
- `test_short_remarks_are_refused`
- `test_whitespace_padding_does_not_count_towards_the_minimum`

## RetentionTests (9)

- `test_a_recent_complaint_is_left_out_of_the_archive`
- `test_archiving_deletes_nothing`
- `test_only_records_past_three_years_are_expired`
- `test_the_application_cannot_purge_the_audit_trail`
- `test_the_application_cannot_purge_the_complaint_either`
- `test_the_archive_holds_the_record_and_its_audit_trail`
- `test_the_command_archives_and_says_nothing_was_deleted`
- `test_the_module_states_that_the_purge_is_not_its_job`
- `test_the_privileged_purge_script_exists_and_restores_the_guard`

## RoleAdminTests (5)

- `test_profiles_are_listed_on_their_own`
- `test_role_can_be_changed_through_the_admin`
- `test_user_admin_offers_the_role_inline`
- `test_user_admin_page_loads`
- `test_user_list_shows_the_scms_role`

## RoutingFlowTests (8)

- `test_complaint_appears_in_the_mapped_department_queue`
- `test_complaint_is_absent_from_other_department_queues`
- `test_inactive_department_leaves_the_complaint_unassigned`
- `test_lodging_assigns_the_mapped_department`
- `test_lodging_sets_the_sla_from_the_category`
- `test_remapping_a_category_reroutes_later_complaints`
- `test_routing_happens_before_the_response_returns`
- `test_routing_is_recorded_in_the_audit_trail`

## RoutingTests (2)

- `test_category_carries_its_department`
- `test_changing_mapping_needs_no_code_change`

## SearchTests (19)

- `test_a_backwards_date_range_is_rejected`
- `test_a_matching_combination_returns_the_row`
- `test_complainant_cannot_reach_the_queue_at_all`
- `test_date_range_before_today_excludes_everything`
- `test_date_range_includes_today`
- `test_department_admin_cannot_search_outside_their_department`
- `test_department_admin_search_still_finds_their_own`
- `test_filter_by_category`
- `test_filter_by_department`
- `test_filter_by_reference_id`
- `test_filter_by_status`
- `test_filters_combine_as_and_not_or`
- `test_keyword_cannot_surface_another_departments_complaint`
- `test_keyword_is_case_insensitive`
- `test_keyword_matches_the_description`
- `test_keyword_matches_the_location`
- `test_keyword_matches_the_title`
- `test_keyword_with_no_match_returns_nothing`
- `test_no_filters_returns_everything_in_scope`

## SecureEvidenceStorageTests (14)

- `test_a_configured_scanner_sees_every_upload`
- `test_a_flagged_upload_is_refused`
- `test_a_php_file_is_rejected`
- `test_a_stored_file_is_not_reachable_by_url`
- `test_an_executable_is_rejected`
- `test_an_image_renamed_to_php_is_rejected`
- `test_download_forbids_content_type_sniffing`
- `test_download_is_served_as_an_attachment`
- `test_no_malware_scanner_is_configured_by_default`
- `test_php_disguised_as_an_image_is_rejected`
- `test_the_declared_type_is_the_detected_one`
- `test_the_scanner_does_not_consume_the_upload`
- `test_the_stored_name_is_random_not_the_uploaded_one`
- `test_uploads_are_stored_outside_the_source_tree`

## SessionExpiryTests (4)

- `test_expiry_is_measured_from_last_request`
- `test_idle_window_is_thirty_minutes`
- `test_request_after_idle_window_is_no_longer_authenticated`
- `test_request_inside_idle_window_stays_authenticated`

## SlaTests (2)

- `test_deadline_uses_category_sla_hours`
- `test_different_categories_give_different_deadlines`

## StatusModelTests (3)

- `test_new_complaint_starts_open`
- `test_priority_set_matches_srs`
- `test_status_set_matches_srs`

## StatusTrackingTests (18)

- `test_agent_can_move_the_complaint_along`
- `test_closed_is_terminal`
- `test_complainant_cannot_change_status`
- `test_complainant_sees_the_current_status`
- `test_every_srs_status_appears_in_the_state_machine`
- `test_guarded_statuses_are_not_offered`
- `test_history_is_newest_first`
- `test_illegal_transition_is_refused`
- `test_legal_transition_is_recorded`
- `test_pending_information_returns_to_in_progress`
- `test_resolved_cannot_be_reached_generically`
- `test_resolved_is_still_legal_in_the_state_machine`
- `test_tracking_page_shows_dated_transition_history`
- `test_tracking_page_shows_sub_category_and_location`
- `test_tracking_page_shows_the_category_name`
- `test_transition_to_the_current_status_is_refused`
- `test_view_does_not_offer_an_illegal_move`
- `test_view_rejects_an_illegal_move_posted_directly`

## TemplateCommentLeakTests (1)

- `test_no_page_leaks_its_template_source`

## UrlTamperingSweepTests (7)

- `test_a_stranger_is_denied_every_route`
- `test_an_admin_of_another_department_is_denied_every_route`
- `test_an_agent_from_another_department_is_denied_every_route`
- `test_anonymous_is_sent_to_login_not_shown_a_403`
- `test_every_complaint_route_is_swept`
- `test_the_owner_cannot_use_the_staff_routes`
- `test_the_right_department_admin_is_allowed_through`

## ViewAccessTests (7)

- `test_agent_from_another_department_gets_403`
- `test_agent_queue_shows_only_assigned`
- `test_anonymous_is_redirected_to_login`
- `test_complainant_cannot_open_staff_queue`
- `test_other_complainant_gets_403`
- `test_owner_gets_200`
- `test_staff_cannot_lodge_complaints`

