select
  ticket_id,
  AI.GENERATE(
    concat('Summarize: ', body),
    model_params => JSON '{"generation_config":{"max_output_tokens":32,"thinking_config":{"thinking_budget":0}}}'
  ).result as summary
from {{ ref('stg_tickets') }}
where status = 'open'
