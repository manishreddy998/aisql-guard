{{ config(materialized='table') }}

select
  ticket_id,
  AI.GENERATE(
    (select as struct src.*),
    model_params => JSON '{"generation_config":{"max_output_tokens":64,"thinking_config":{"thinking_budget":0}}}'
  ).result as payload
from {{ ref('stg_tickets') }} as src
where priority = 'high'
