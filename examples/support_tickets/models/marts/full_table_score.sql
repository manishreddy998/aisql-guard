{{ config(materialized='table') }}

select
  ticket_id,
  AI.SCORE(
    ('Rate urgency from 0 to 1: ', body),
    model_params => JSON '{"generation_config":{"thinking_config":{"thinking_budget":0}}}'
  ) as urgency
from {{ ref('stg_tickets') }}
