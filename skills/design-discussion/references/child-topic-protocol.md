# Child Topic and Continuation Protocol

Read this reference only when the user asks to split a mature topic or continue
an unavailable conversation.

Do not implement child creation or continuation through bootstrap. Those
actions require typed handoff, attempt and binding operations that are outside
Ticket 01.

When the later protocol is available, child creation must first persist a child
topic and handoff intent, then call the external task-creation surface, then
bind only the verified real conversation reference. A continuation retains the
same `topic_id`, creates a new conversation, records `continuation_of`, and
atomically supersedes the previous active binding. Never claim restoration of
an unavailable conversation.

Until those operations exist, keep the current topic at its last verified
state and explain that splitting or continuation cannot be recorded safely.
