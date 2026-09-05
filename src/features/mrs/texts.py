"""MRs-local UI strings: list, card, action buttons, toasts."""

MRS_LIST_HEADER = "Open MR в <b>{scope_path}</b> ({count}):"
MRS_LIST_EMPTY = "Открытых MR в этом scope нет."

MRS_STATUS_READY = "✅ Ready"
MRS_STATUS_DRAFT = "🚧 Draft"
MRS_STATUS_MERGED = "🎉 Merged"
MRS_STATUS_CLOSED = "❌ Closed"

MRS_FLAG_CONFLICTS = "⚠️ <b>Есть конфликты</b>"
MRS_APPROVALS_EMPTY = "Approved by: —"
MRS_APPROVALS_LINE = "Approved by: {names}"

# ---------- buttons ----------

MRS_BTN_BACK_TO_LIST = "← К списку MR"
MRS_BTN_APPROVE_ON = "✅ Approved"
MRS_BTN_APPROVE_OFF = "⬜ Approve"
MRS_BTN_MERGE = "🚀 Merge"
MRS_BTN_APPROVE_AS = "🎭 Approve as…"
MRS_BTN_TOGGLE_ON = "✅"
MRS_BTN_TOGGLE_OFF = "⬜"

# ---------- proxy screen ----------

MRS_PROXY_ASK = "От чьего имени поставить/снять апрув?"
MRS_PROXY_EMPTY = "На этом GitLab-инстансе нет других пользователей с подключением."

# ---------- toasts ----------

MRS_TOAST_APPROVED = "✅ Approved!"
MRS_TOAST_UNAPPROVED = "↩️ Approve отозван"
MRS_TOAST_MERGED = "🎉 MR замёржен"
MRS_TOAST_MR_CLOSED = "MR закрыт или замёржен."
MRS_TOAST_SHA_MISMATCH = "SHA устарел — открой /mrs заново."
MRS_TOAST_SELF_APPROVAL = "GitLab запретил апрув — этот пользователь автор MR."
MRS_TOAST_MERGE_CONFLICT = "❌ Есть конфликты — сначала реши их."
MRS_TOAST_MERGE_NOT_READY = "MR не готов к merge: {reason}"
MRS_TOAST_INVALID_PAT = (
    "GitLab отклонил PAT (неверный, отозван или нет нужных scopes)."
)
