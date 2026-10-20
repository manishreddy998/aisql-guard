{{ config(materialized='table') }}

select *
from ML.GENERATE_TEXT(
  model `warehouse.remote_models.ticket_model`,
  (
    select concat('Summarize: ', body) as prompt
    from {{ ref('stg_tickets') }}
    where status = 'open'
    limit 100
  ),
  struct(
    128 as max_output_tokens,
    '{"generation_config":{"thinking_config":{"thinking_budget":0}}}' as model_params
  )
)
