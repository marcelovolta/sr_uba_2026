select *
from albums 
where genre <> 'psychedelia';

select genre, count(*)
from albums
group by 1;

select count(*)
from user_reviews as ur
inner join albums as a
on ur.album_id = a.id
where a.genre = 'ambient';

with reviews_ambient as (
select ur.*
from user_reviews as ur 
inner join albums as a
on ur.album_id = a.id
where a.genre = 'ambient'),



reviews_psy as (
select ur.*
from user_reviews as ur 
inner join albums as a
on ur.album_id = a.id
where a.genre = 'psychedelia')
select count(*)
from users 
where users.username in (select DISTINCT username from reviews_ambient)
and username not in (select DISTINCT username from reviews_psy);
;

--Distribucion usuarios
with t1 as (
select username, count(*) as review_count 
from user_reviews as ur
inner join albums as al 
on ur.album_id = al.id
where al.genre = 'ambient'
group by 1)
select review_count, count(DISTINCT username) as users 
from t1
group by 1
order by 1 desc;

--Distribucion albums
with t1 as (
select album_id, count(*) as review_count 
from user_reviews as ur
inner join albums as al 
on ur.album_id = al.id
where al.genre = 'ambient'
group by 1)
select review_count, count(DISTINCT album_id) as albums
from t1
group by 1
order by 1 desc;
