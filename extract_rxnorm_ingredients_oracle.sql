-- Leaner single-statement Oracle SQL equivalent of extract_rxnorm_ingredients.py
-- using RxNorm standalone tables RXNCONSO / RXNREL / RXNSAT.
-- Output is flattened: one row per ingredient/SCDC/SCD plus related term-type branches.

with
rxnorm_terms_raw as (
    select c.rxcui,
           c.tty,
           c.str as name,
           c.ts,
           c.rxaui
    from rxnconso c
    where c.sab = 'RXNORM'
      and c.suppress = 'N'
      and c.lat = 'ENG'
      and c.tty in (
            'IN', 'PIN', 'MIN',
            'SCDC', 'SCD', 'GPCK', 'BPCK', 'SBD', 'BN',
            'SCDF', 'SCDG', 'SBDG', 'DF', 'DFG', 'CDC',
            'PSN', 'SY'
      )
),
preferred_terms as (
    select rxcui, tty, name
    from (
        select r.rxcui,
               r.tty,
               r.name,
               row_number() over (
                   partition by r.rxcui, r.tty
                   order by case when r.ts = 'P' then 0 else 1 end,
                            r.rxaui,
                            r.name
               ) as rn
        from rxnorm_terms_raw r
    )
    where rn = 1
),
psn_terms as (
    select x.rxcui,
           listagg(x.name, ' | ') within group (order by lower(x.name), x.name) as psn_names
    from (
        select distinct rxcui, name
        from rxnorm_terms_raw
        where tty = 'PSN'
    ) x
    group by x.rxcui
),
sy_terms as (
    select x.rxcui,
           listagg(x.name, ' | ') within group (order by lower(x.name), x.name) as sy_names
    from (
        select distinct rxcui, name
        from rxnorm_terms_raw
        where tty = 'SY'
    ) x
    group by x.rxcui
),
primary_terms as (
    select p.rxcui,
           max(case when p.tty = 'IN'   then p.name end) as in_name,
           max(case when p.tty = 'PIN'  then p.name end) as pin_name,
           max(case when p.tty = 'MIN'  then p.name end) as min_name,
           max(case when p.tty = 'SCDC' then p.name end) as scdc_name,
           max(case when p.tty = 'SCD'  then p.name end) as scd_name,
           max(case when p.tty = 'GPCK' then p.name end) as gpck_name,
           max(case when p.tty = 'BPCK' then p.name end) as bpck_name,
           max(case when p.tty = 'SBD'  then p.name end) as sbd_name,
           max(case when p.tty = 'BN'   then p.name end) as bn_name,
           max(case when p.tty = 'SCDF' then p.name end) as scdf_name,
           max(case when p.tty = 'SCDG' then p.name end) as scdg_name,
           max(case when p.tty = 'SBDG' then p.name end) as sbdg_name,
           max(case when p.tty = 'DF'   then p.name end) as df_name,
           max(case when p.tty = 'DFG'  then p.name end) as dfg_name,
           max(case when p.tty = 'CDC'  then p.name end) as cdc_name
    from preferred_terms p
    where p.tty in (
        'IN', 'PIN', 'MIN',
        'SCDC', 'SCD', 'GPCK', 'BPCK', 'SBD', 'BN',
        'SCDF', 'SCDG', 'SBDG', 'DF', 'DFG', 'CDC'
    )
    group by p.rxcui
),
concept_dim as (
    select t.rxcui,
           case
               when t.in_name   is not null then 'IN'
               when t.pin_name  is not null then 'PIN'
               when t.min_name  is not null then 'MIN'
               when t.scdc_name is not null then 'SCDC'
               when t.scd_name  is not null then 'SCD'
               when t.gpck_name is not null then 'GPCK'
               when t.bpck_name is not null then 'BPCK'
               when t.sbd_name  is not null then 'SBD'
               when t.bn_name   is not null then 'BN'
               when t.scdf_name is not null then 'SCDF'
               when t.scdg_name is not null then 'SCDG'
               when t.sbdg_name is not null then 'SBDG'
               when t.df_name   is not null then 'DF'
               when t.dfg_name  is not null then 'DFG'
               when t.cdc_name  is not null then 'CDC'
           end as primary_tty,
           coalesce(
               t.in_name, t.pin_name, t.min_name,
               t.scdc_name, t.scd_name, t.gpck_name, t.bpck_name, t.sbd_name, t.bn_name,
               t.scdf_name, t.scdg_name, t.sbdg_name, t.df_name, t.dfg_name, t.cdc_name
           ) as primary_name,
           p.psn_names,
           s.sy_names
    from primary_terms t
    left join psn_terms p
      on p.rxcui = t.rxcui
    left join sy_terms s
      on s.rxcui = t.rxcui
),
ingredient_concepts as (
    select c.rxcui,
           c.primary_tty,
           c.primary_name,
           c.psn_names,
           c.sy_names
    from concept_dim c
    where c.primary_tty in ('IN', 'PIN', 'MIN')
),
unii_map as (
    select c.rxcui,
           min(c.code) keep (dense_rank first order by c.code) as unii
    from rxnconso c
    where c.sab = 'MTHSPL'
      and c.tty = 'SU'
      and c.code is not null
    group by c.rxcui
),
edges as (
    select r.rxcui1,
           r.rxcui2,
           r.rela,
           c1.primary_tty as tty1,
           c2.primary_tty as tty2
    from rxnrel r
    join concept_dim c1
      on c1.rxcui = r.rxcui1
    join concept_dim c2
      on c2.rxcui = r.rxcui2
    where r.sab = 'RXNORM'
      and r.stype1 = 'CUI'
      and r.stype2 = 'CUI'
),
ing_to_scdc_direct as (
    select distinct e.rxcui1 as ingredient_rxcui, e.rxcui2 as scdc_rxcui
    from edges e
    where e.rela in ('has_ingredient', 'ingredient_of')
      and e.tty1 in ('IN', 'PIN', 'MIN')
      and e.tty2 = 'SCDC'

    union

    select distinct e.rxcui2 as ingredient_rxcui, e.rxcui1 as scdc_rxcui
    from edges e
    where e.rela in ('has_ingredient', 'ingredient_of')
      and e.tty2 in ('IN', 'PIN', 'MIN')
      and e.tty1 = 'SCDC'

    union

    select distinct e.rxcui1 as ingredient_rxcui, e.rxcui2 as scdc_rxcui
    from edges e
    where e.rela in ('has_precise_ingredient', 'precise_ingredient_of')
      and e.tty1 = 'PIN'
      and e.tty2 = 'SCDC'

    union

    select distinct e.rxcui2 as ingredient_rxcui, e.rxcui1 as scdc_rxcui
    from edges e
    where e.rela in ('has_precise_ingredient', 'precise_ingredient_of')
      and e.tty2 = 'PIN'
      and e.tty1 = 'SCDC'
),
scdc_to_scd as (
    select distinct e.rxcui2 as scdc_rxcui, e.rxcui1 as scd_rxcui
    from edges e
    where e.rela = 'constitutes'
      and e.tty1 = 'SCD'
      and e.tty2 = 'SCDC'

    union

    select distinct e.rxcui1 as scdc_rxcui, e.rxcui2 as scd_rxcui
    from edges e
    where e.rela = 'constitutes'
      and e.tty1 = 'SCDC'
      and e.tty2 = 'SCD'
),
scd_to_scdc as (
    select distinct s.scd_rxcui, s.scdc_rxcui
    from scdc_to_scd s
),
pin_to_scdc_inherited as (
    select distinct e.rxcui2 as pin_rxcui, d.scdc_rxcui
    from edges e
    join ing_to_scdc_direct d
      on d.ingredient_rxcui = e.rxcui1
    where e.rela in ('has_precise_ingredient', 'precise_ingredient_of')
      and e.tty1 = 'IN'
      and e.tty2 = 'PIN'

    union

    select distinct e.rxcui1 as pin_rxcui, d.scdc_rxcui
    from edges e
    join ing_to_scdc_direct d
      on d.ingredient_rxcui = e.rxcui2
    where e.rela in ('has_precise_ingredient', 'precise_ingredient_of')
      and e.tty2 = 'IN'
      and e.tty1 = 'PIN'
),
min_to_scdc as (
    select distinct e.rxcui1 as min_rxcui, d.scdc_rxcui
    from edges e
    join ing_to_scdc_direct d
      on d.ingredient_rxcui = e.rxcui2
    where e.rela in ('has_ingredient', 'has_ingredients', 'ingredients_of')
      and e.tty1 = 'MIN'
      and e.tty2 = 'IN'

    union

    select distinct e.rxcui2 as min_rxcui, d.scdc_rxcui
    from edges e
    join ing_to_scdc_direct d
      on d.ingredient_rxcui = e.rxcui1
    where e.rela in ('has_ingredient', 'has_ingredients', 'ingredients_of')
      and e.tty2 = 'MIN'
      and e.tty1 = 'IN'

    union

    select distinct e.rxcui1 as min_rxcui, s.scdc_rxcui
    from edges e
    join scd_to_scdc s
      on s.scd_rxcui = e.rxcui2
    where e.rela in ('has_ingredient', 'has_ingredients', 'ingredients_of')
      and e.tty1 = 'MIN'
      and e.tty2 = 'SCD'

    union

    select distinct e.rxcui2 as min_rxcui, s.scdc_rxcui
    from edges e
    join scd_to_scdc s
      on s.scd_rxcui = e.rxcui1
    where e.rela in ('has_ingredient', 'has_ingredients', 'ingredients_of')
      and e.tty2 = 'MIN'
      and e.tty1 = 'SCD'
),
ingredient_to_scdc as (
    select distinct d.ingredient_rxcui, d.scdc_rxcui
    from ing_to_scdc_direct d
    join concept_dim c
      on c.rxcui = d.ingredient_rxcui
     and c.primary_tty = 'IN'

    union

    select distinct d.ingredient_rxcui, d.scdc_rxcui
    from ing_to_scdc_direct d
    join concept_dim c
      on c.rxcui = d.ingredient_rxcui
     and c.primary_tty = 'PIN'

    union

    select distinct p.pin_rxcui as ingredient_rxcui, p.scdc_rxcui
    from pin_to_scdc_inherited p
    join concept_dim c
      on c.rxcui = p.pin_rxcui
     and c.primary_tty = 'PIN'

    union

    select distinct m.min_rxcui as ingredient_rxcui, m.scdc_rxcui
    from min_to_scdc m
    join concept_dim c
      on c.rxcui = m.min_rxcui
     and c.primary_tty = 'MIN'
),
ndc_values as (
    select distinct s.rxcui, s.atv as ndc
    from rxnsat s
    where s.sab = 'RXNORM'
      and s.atn = 'NDC'
      and s.suppress = 'N'
      and s.rxcui is not null
      and s.atv is not null
),
scd_to_gpck as (
    select distinct e.rxcui1 as scd_rxcui, e.rxcui2 as gpck_rxcui
    from edges e
    where e.rela in ('contains', 'contained_in')
      and e.tty1 = 'SCD'
      and e.tty2 = 'GPCK'

    union

    select distinct e.rxcui2 as scd_rxcui, e.rxcui1 as gpck_rxcui
    from edges e
    where e.rela in ('contains', 'contained_in')
      and e.tty2 = 'SCD'
      and e.tty1 = 'GPCK'
),
scd_to_bpck as (
    select distinct e.rxcui1 as scd_rxcui, e.rxcui2 as bpck_rxcui
    from edges e
    where e.rela in ('contains', 'contained_in')
      and e.tty1 = 'SCD'
      and e.tty2 = 'BPCK'

    union

    select distinct e.rxcui2 as scd_rxcui, e.rxcui1 as bpck_rxcui
    from edges e
    where e.rela in ('contains', 'contained_in')
      and e.tty2 = 'SCD'
      and e.tty1 = 'BPCK'
),
scd_to_sbd as (
    select distinct e.rxcui1 as scd_rxcui, e.rxcui2 as sbd_rxcui
    from edges e
    where e.rela in ('has_tradename', 'tradename_of')
      and e.tty1 = 'SCD'
      and e.tty2 = 'SBD'

    union

    select distinct e.rxcui2 as scd_rxcui, e.rxcui1 as sbd_rxcui
    from edges e
    where e.rela in ('has_tradename', 'tradename_of')
      and e.tty2 = 'SCD'
      and e.tty1 = 'SBD'
),
sbd_to_bn as (
    select distinct e.rxcui2 as sbd_rxcui, e.rxcui1 as bn_rxcui
    from edges e
    where e.rela in ('has_ingredient', 'ingredient_of')
      and e.tty1 = 'BN'
      and e.tty2 = 'SBD'

    union

    select distinct e.rxcui1 as sbd_rxcui, e.rxcui2 as bn_rxcui
    from edges e
    where e.rela in ('has_ingredient', 'ingredient_of')
      and e.tty2 = 'BN'
      and e.tty1 = 'SBD'
),
scd_to_scdf as (
    select distinct e.rxcui1 as scd_rxcui, e.rxcui2 as scdf_rxcui
    from edges e
    where e.rela in ('isa', 'inverse_isa')
      and e.tty1 = 'SCD'
      and e.tty2 = 'SCDF'

    union

    select distinct e.rxcui2 as scd_rxcui, e.rxcui1 as scdf_rxcui
    from edges e
    where e.rela in ('isa', 'inverse_isa')
      and e.tty2 = 'SCD'
      and e.tty1 = 'SCDF'
),
scdf_to_df as (
    select distinct e.rxcui1 as scdf_rxcui, e.rxcui2 as df_rxcui
    from edges e
    where e.rela in ('has_dose_form', 'dose_form_of')
      and e.tty1 = 'SCDF'
      and e.tty2 = 'DF'

    union

    select distinct e.rxcui2 as scdf_rxcui, e.rxcui1 as df_rxcui
    from edges e
    where e.rela in ('has_dose_form', 'dose_form_of')
      and e.tty2 = 'SCDF'
      and e.tty1 = 'DF'
),
scd_to_scdg as (
    select distinct e.rxcui1 as scd_rxcui, e.rxcui2 as scdg_rxcui
    from edges e
    where e.rela in ('isa', 'inverse_isa')
      and e.tty1 = 'SCD'
      and e.tty2 = 'SCDG'

    union

    select distinct e.rxcui2 as scd_rxcui, e.rxcui1 as scdg_rxcui
    from edges e
    where e.rela in ('isa', 'inverse_isa')
      and e.tty2 = 'SCD'
      and e.tty1 = 'SCDG'
),
scdg_to_dfg as (
    select distinct e.rxcui1 as scdg_rxcui, e.rxcui2 as dfg_rxcui
    from edges e
    where e.rela in ('has_doseformgroup', 'doseformgroup_of')
      and e.tty1 = 'SCDG'
      and e.tty2 = 'DFG'

    union

    select distinct e.rxcui2 as scdg_rxcui, e.rxcui1 as dfg_rxcui
    from edges e
    where e.rela in ('has_doseformgroup', 'doseformgroup_of')
      and e.tty2 = 'SCDG'
      and e.tty1 = 'DFG'
),
sbd_to_sbdg as (
    select distinct e.rxcui1 as sbd_rxcui, e.rxcui2 as sbdg_rxcui
    from edges e
    where e.rela in ('isa', 'inverse_isa')
      and e.tty1 = 'SBD'
      and e.tty2 = 'SBDG'

    union

    select distinct e.rxcui2 as sbd_rxcui, e.rxcui1 as sbdg_rxcui
    from edges e
    where e.rela in ('isa', 'inverse_isa')
      and e.tty2 = 'SBD'
      and e.tty1 = 'SBDG'
),
sbdg_to_dfg as (
    select distinct e.rxcui1 as sbdg_rxcui, e.rxcui2 as dfg_rxcui
    from edges e
    where e.rela in ('has_doseformgroup', 'doseformgroup_of')
      and e.tty1 = 'SBDG'
      and e.tty2 = 'DFG'

    union

    select distinct e.rxcui2 as sbdg_rxcui, e.rxcui1 as dfg_rxcui
    from edges e
    where e.rela in ('has_doseformgroup', 'doseformgroup_of')
      and e.tty2 = 'SBDG'
      and e.tty1 = 'DFG'
)
select distinct
       ing.rxcui                 as ingredient_rxcui,
       ing.primary_tty           as ingredient_tty,
       ing.primary_name          as ingredient_name,
       u.unii                    as ingredient_unii,
       ing.psn_names             as ingredient_psn_names,
       ing.sy_names              as ingredient_sy_names,
       scdc.rxcui                as scdc_rxcui,
       scdc.primary_name         as scdc_name,
       scdc.psn_names            as scdc_psn_names,
       scdc.sy_names             as scdc_sy_names,
       scdf.rxcui                as scdf_rxcui,
       scdf.primary_name         as scdf_name,
       scdf.psn_names            as scdf_psn_names,
       scdf.sy_names             as scdf_sy_names,
       df.rxcui                  as df_rxcui,
       df.primary_name           as df_name,
       df.psn_names              as df_psn_names,
       df.sy_names               as df_sy_names,
       scdg.rxcui                as scdg_rxcui,
       scdg.primary_name         as scdg_name,
       scdg.psn_names            as scdg_psn_names,
       scdg.sy_names             as scdg_sy_names,
       dfg.rxcui                 as dfg_rxcui,
       dfg.primary_name          as dfg_name,
       dfg.psn_names             as dfg_psn_names,
       dfg.sy_names              as dfg_sy_names,
       cast(null as varchar2(8))    as cdc_rxcui,
       cast(null as varchar2(4000)) as cdc_name,
       cast(null as varchar2(4000)) as cdc_psn_names,
       cast(null as varchar2(4000)) as cdc_sy_names,
       scd.rxcui                 as scd_rxcui,
       scd.primary_name          as scd_name,
       scd.psn_names             as scd_psn_names,
       scd.sy_names              as scd_sy_names,
       scd_ndc.ndc               as scd_ndc,
       gpck.rxcui                as gpck_rxcui,
       gpck.primary_name         as gpck_name,
       gpck.psn_names            as gpck_psn_names,
       gpck.sy_names             as gpck_sy_names,
       gpck_ndc.ndc              as gpck_ndc,
       bpck.rxcui                as bpck_rxcui,
       bpck.primary_name         as bpck_name,
       bpck.psn_names            as bpck_psn_names,
       bpck.sy_names             as bpck_sy_names,
       bpck_ndc.ndc              as bpck_ndc,
       sbd.rxcui                 as sbd_rxcui,
       sbd.primary_name          as sbd_name,
       sbd.psn_names             as sbd_psn_names,
       sbd.sy_names              as sbd_sy_names,
       sbd_ndc.ndc               as sbd_ndc,
       sbdg.rxcui                as sbdg_rxcui,
       sbdg.primary_name         as sbdg_name,
       sbdg.psn_names            as sbdg_psn_names,
       sbdg.sy_names             as sbdg_sy_names,
       bn.rxcui                  as bn_rxcui,
       bn.primary_name           as bn_name,
       bn.psn_names              as bn_psn_names,
       bn.sy_names               as bn_sy_names
from ingredient_to_scdc i2s
join concept_dim ing
  on ing.rxcui = i2s.ingredient_rxcui
left join unii_map u
  on u.rxcui = ing.rxcui
left join concept_dim scdc
  on scdc.rxcui = i2s.scdc_rxcui
left join scdc_to_scd s2s
  on s2s.scdc_rxcui = i2s.scdc_rxcui
left join concept_dim scd
  on scd.rxcui = s2s.scd_rxcui
left join ndc_values scd_ndc
  on scd_ndc.rxcui = s2s.scd_rxcui
left join scd_to_scdf s2scdf
  on s2scdf.scd_rxcui = s2s.scd_rxcui
left join concept_dim scdf
  on scdf.rxcui = s2scdf.scdf_rxcui
left join scdf_to_df s2df
  on s2df.scdf_rxcui = s2scdf.scdf_rxcui
left join concept_dim df
  on df.rxcui = s2df.df_rxcui
left join scd_to_scdg s2scdg
  on s2scdg.scd_rxcui = s2s.scd_rxcui
left join concept_dim scdg
  on scdg.rxcui = s2scdg.scdg_rxcui
left join scdg_to_dfg scdg_dfg
  on scdg_dfg.scdg_rxcui = s2scdg.scdg_rxcui
left join scd_to_gpck s2gpck
  on s2gpck.scd_rxcui = s2s.scd_rxcui
left join concept_dim gpck
  on gpck.rxcui = s2gpck.gpck_rxcui
left join ndc_values gpck_ndc
  on gpck_ndc.rxcui = s2gpck.gpck_rxcui
left join scd_to_bpck s2bpck
  on s2bpck.scd_rxcui = s2s.scd_rxcui
left join concept_dim bpck
  on bpck.rxcui = s2bpck.bpck_rxcui
left join ndc_values bpck_ndc
  on bpck_ndc.rxcui = s2bpck.bpck_rxcui
left join scd_to_sbd s2sbd
  on s2sbd.scd_rxcui = s2s.scd_rxcui
left join concept_dim sbd
  on sbd.rxcui = s2sbd.sbd_rxcui
left join ndc_values sbd_ndc
  on sbd_ndc.rxcui = s2sbd.sbd_rxcui
left join sbd_to_sbdg s2sbdg
  on s2sbdg.sbd_rxcui = s2sbd.sbd_rxcui
left join concept_dim sbdg
  on sbdg.rxcui = s2sbdg.sbdg_rxcui
left join sbdg_to_dfg sbdg_dfg
  on sbdg_dfg.sbdg_rxcui = s2sbdg.sbdg_rxcui
left join concept_dim dfg
  on dfg.rxcui = coalesce(scdg_dfg.dfg_rxcui, sbdg_dfg.dfg_rxcui)
left join sbd_to_bn s2bn
  on s2bn.sbd_rxcui = s2sbd.sbd_rxcui
left join concept_dim bn
  on bn.rxcui = s2bn.bn_rxcui
order by lower(ing.primary_name),
         ing.rxcui,
         scdc.rxcui,
         scdf.rxcui,
         df.rxcui,
         scdg.rxcui,
         dfg.rxcui,
         scd.rxcui,
         gpck.rxcui,
         bpck.rxcui,
         sbd.rxcui,
         sbdg.rxcui,
         bn.rxcui,
         scd_ndc.ndc,
         gpck_ndc.ndc,
         bpck_ndc.ndc,
         sbd_ndc.ndc;
