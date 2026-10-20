{{ config(materialized='incremental', unique_key='ticket_id') }}

select
  ticket_id,
  AI.GENERATE(
    concat('Summarize this ticket in one sentence: ', body),
    model_params => JSON '{"generation_config":{"max_output_tokens":128,"thinking_config":{"thinking_budget":0}}}'
  ).result as summary
from {{ ref('stg_tickets') }} as src
where status = 'closed'
