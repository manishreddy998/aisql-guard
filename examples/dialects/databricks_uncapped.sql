select
  ticket_id,
  ai_query('llama-3-3-70b', concat('Summarize: ', body)) as summary
from tickets
where status = 'open'
