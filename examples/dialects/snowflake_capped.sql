select
  ticket_id,
  AI_COMPLETE('llama3.1-8b', body, {'max_tokens': 64}) as summary
from tickets
where status = 'open'
