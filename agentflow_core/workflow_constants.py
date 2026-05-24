REVIEW_DECISION_REQUIRED_KEYS = {"next_action", "reason"}
ALLOWED_NEXT_ACTIONS = {"rerun_execution", "human_review", "done"}

LOOP_DETECTOR_REQUIRED_KEYS = {"next_action", "reason"}
ALLOWED_LOOP_DETECTOR_ACTIONS = {"continue", "human_review"}

AUTONOMY_DECISION_REQUIRED_KEYS = {"next_action", "reason", "resume_feedback"}
ALLOWED_AUTONOMY_DECISION_ACTIONS = {"auto_resolve", "human_review"}
