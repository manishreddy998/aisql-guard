{{ config(materialized='table') }}

select
  ticket_id,
  AI.GENERATE(
    concat('Summarize this ticket in one sentence: ', body),
    model_params => JSON '{"generation_config":{"max_output_tokens":128}}'
  ).result as summary
from {{ ref('stg_tickets') }}
where status = 'closed'
