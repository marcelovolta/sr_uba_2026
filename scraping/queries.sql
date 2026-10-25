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
--and username not in (select DISTINCT username from reviews_psy);

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


CREATE TABLE IF NOT EXISTS vistas (
id_lector TEXT, 
id_libro TEXT,
fecha datetime,
PRIMARY KEY (id_lector, id_libro)
);

select genre, count(*)
from albums 
group by 1;;



--Recomendar por perfiles
WITH album_all_genres AS (
	SELECT 
		alb.id AS album_id, 
		agn.genre_id
	FROM albums AS alb 
	INNER JOIN album_genres AS agn
		ON alb.id = agn.album_id
), 

album_all_artists AS (
	SELECT 
		alb.id AS album_id, 
		aar.artist_id
	FROM albums AS alb 
	INNER JOIN album_artists AS aar
		ON alb.id = aar.album_id
), 

rev_count AS (
	SELECT 
		ure.username, COUNT(*) as rev_count
	FROM user_reviews AS ure 
	GROUP BY 1
),
	
pref_genres AS (
        SELECT  
			ag.genre_id, 
			AVG(ure.rating) AS genre_evals_avg, 
			COUNT(*) AS genre_evals_count,
			(0.8 * AVG(ure.rating)) + (0.2 * COUNT(*)) AS genre_corr_rating
        FROM user_reviews AS ure
		INNER JOIN album_genres AS ag
		ON ure.album_id = ag.album_id
        WHERE ure.username = '0Incarnadine0' --? --id_oyente
			AND COALESCE(ure.rating, 0) > 0
		GROUP BY 1
		HAVING genre_evals_count > 2 --? --hiperparámetro
 ),
		
 pref_artists AS (
        SELECT 
			ai.artist_id, 
			AVG(ure.rating) AS artist_evals_avg,
			COUNT(*) AS artist_eval_count,
			(0.8 * AVG(ure.rating)) + (0.2 * COUNT(*)) AS artist_corr_rating
        FROM user_reviews AS ure
		INNER JOIN album_all_artists AS ai
		ON ure.album_id = ai.album_id
		WHERE ure.username = '0Incarnadine0' --? 
			AND COALESCE(ure.rating, 0) > 0
		GROUP BY 1
		HAVING artist_eval_count > 2 --? --hiperparametro
),

popular_albums AS (
	SELECT 
		album_id, 
		AVG(ur.rating) AS avg_rating,
	    COUNT(*) AS review_count,
        (0.8 * AVG(ur.rating)) + (0.2 * COUNT(*)) AS corr_rating		
	FROM user_reviews AS ur 
	GROUP BY 1
	HAVING review_count > 5
	ORDER BY 4 DESC
),

fan_out_by_genre AS (
	SELECT 
		pal.album_id,
		pal.corr_rating,
		SUM(pge.genre_corr_rating) AS genre_corr_rating 
	FROM popular_albums AS pal
	INNER JOIN album_all_genres AS age
	ON pal.album_id = age.album_id 
	LEFT JOIN pref_genres AS pge
	ON age.genre_id = pge.genre_id
	GROUP BY 1, 2
),

fan_out_by_artist AS (
	SELECT 
		pal.album_id,
		pal.corr_rating,
		SUM(par.artist_corr_rating) AS artist_corr_rating
	FROM popular_albums AS pal
	INNER JOIN album_all_artists AS aar
	ON pal.album_id = aar.album_id
	LEFT JOIN pref_genres AS pge
	ON age.genre_id = pge.genre_id
	LEFT JOIN pref_artists AS par 
	ON aar.artist_id = par.artist_id
	GROUP BY 1, 2
)

SELECT 
	fog.album_id, 
	fog.corr_rating, 
	fog.genre_corr_rating,
	foa.artist_corr_rating,
	(0.4 * COALESCE(pge.genre_corr_rating, 0)) +
     (0.4 * COALESCE(par.artist_corr_rating, 0)) +
     (0.2 * pal.corr_rating) AS global_rating	
FROM fan_out_by_genre AS fog
INNER JOIN fan_out_by_artist AS foa
ON fog.album_id = foa.album_id 
ORDER BY album_id, global_rating,









WITH album_all_genres AS (
    SELECT album_id, genre_id
    FROM album_genres
),

album_all_artists AS (
    SELECT album_id, artist_id
    FROM album_artists
),

pref_genres AS (
    SELECT
        ag.genre_id,
        AVG(ure.rating) AS genre_evals_avg,
        COUNT(*) AS genre_evals_count,
        (0.8 * AVG(ure.rating)) + (0.2 * COUNT(*)) AS genre_corr_rating
    FROM user_reviews AS ure
    INNER JOIN album_genres AS ag
        ON ure.album_id = ag.album_id
    WHERE ure.username = '0Incarnadine0'   -- id_oyente
      AND COALESCE(ure.rating, 0) > 0
    GROUP BY ag.genre_id
    HAVING COUNT(*) > 2                    -- hiperparámetro
),

pref_artists AS (
    SELECT
        aar.artist_id,
        AVG(ure.rating) AS artist_evals_avg,
        COUNT(*) AS artist_eval_count,
        (0.8 * AVG(ure.rating)) + (0.2 * COUNT(*)) AS artist_corr_rating
    FROM user_reviews AS ure
    INNER JOIN album_all_artists AS aar
        ON ure.album_id = aar.album_id
    WHERE ure.username = '0Incarnadine0'
      AND COALESCE(ure.rating, 0) > 0
    GROUP BY aar.artist_id
    HAVING COUNT(*) > 2                    -- hiperparámetro
),

popular_albums AS (
    SELECT
        album_id,
        AVG(rating) AS avg_rating,
        COUNT(*) AS review_count,
        (0.8 * AVG(rating)) + (0.2 * COUNT(*)) AS corr_rating
    FROM user_reviews
    GROUP BY album_id
    HAVING COUNT(*) > 5
),

fan_out_by_genre AS (
    SELECT
        pal.album_id,
        pal.corr_rating,
        SUM(pge.genre_corr_rating) AS genre_corr_rating
    FROM popular_albums AS pal
    INNER JOIN album_all_genres AS age
        ON pal.album_id = age.album_id
    LEFT JOIN pref_genres AS pge
        ON age.genre_id = pge.genre_id
    GROUP BY pal.album_id, pal.corr_rating
),

fan_out_by_artist AS (
    SELECT
        pal.album_id,
        SUM(par.artist_corr_rating) AS artist_corr_rating
    FROM popular_albums AS pal
    INNER JOIN album_all_artists AS aar
        ON pal.album_id = aar.album_id
    LEFT JOIN pref_artists AS par
        ON aar.artist_id = par.artist_id
    GROUP BY pal.album_id
)

SELECT
    fog.album_id,
    fog.corr_rating,
    fog.genre_corr_rating,
    foa.artist_corr_rating,
    (0.4 * COALESCE(fog.genre_corr_rating, 0))
      + (0.4 * COALESCE(foa.artist_corr_rating, 0))
      + (0.2 * fog.corr_rating) AS global_rating
FROM fan_out_by_genre AS fog
INNER JOIN fan_out_by_artist AS foa
    ON fog.album_id = foa.album_id
ORDER BY global_rating DESC, fog.album_id;


select urev.rating,  acat.*
from user_reviews as urev 
inner join album_catalog as acat
on urev.album_id = acat.album_id
where urev.username = '0Incarnadine0'
order by urev.rating desc;


select urev.rating,  acat.*
from user_reviews as urev 
inner join album_catalog as acat
on urev.album_id = acat.album_id
where urev.username = '0Incarnadine0'
order by urev.rating desc;


--Get coverage
--This query is set to calculate proper coverage
with selected_users as (
select users.username, count(distinct ur.id) as rev_count
from users
inner join user_reviews as ur
on users.username = ur.username 
group by 1
having rev_count >= 20
), 

count_selected_users as (
select count(distinct username) as user_count
from selected_users
),

selected_albums as (
select albums.id as album_id, count(distinct ur.id) as rev_count
from albums 
inner join user_reviews as ur
on albums.id = ur.album_id
group by 1
having rev_count >= 20
), 

count_selected_albums as (
select count(distinct album_id) as album_count
from selected_albums
),

total_matrix as (
select csa.album_count * csu.user_count as denominator, album_count, user_count
from count_selected_users as csu
cross join count_selected_albums as csa
), 

covered_matrix as (
select count(distinct id) as numerator
from user_reviews as ur
where ur.username in (select distinct username from selected_users)
and ur.album_id in (select DISTINCT album_id from selected_albums)
)

select album_count, user_count, numerator, denominator, 100.0 * numerator/denominator as percent_coverage
from covered_matrix
cross join total_matrix;



--Coverage corrected
--Get only albums that are reviewed more than x times
WITH selected_albums AS (
	SELECT 
		al.id AS album_id,
		COUNT(DISTINCT ur.id) AS rev_count
	FROM albums AS al
	INNER JOIN user_reviews AS ur
	ON al.id = ur.album_id
	GROUP BY 1
	HAVING rev_count > 15
), 

--Get only users that have reviewed at least x albums from the pre-selection
selected_users AS (
	SELECT 
		users.username, 
		COUNT(DISTINCT ur.id) AS rev_count
	FROM users
	INNER JOIN user_reviews AS ur
	ON users.username = ur.username
	WHERE ur.album_id IN (SELECT DISTINCT album_id FROM selected_albums)
	GROUP BY 1
	HAVING rev_count >= 15
), 
--Now count
count_selected_users AS (
	SELECT COUNT(DISTINCT su.username) AS user_count
	FROM selected_users AS su
),

count_selected_albums AS (
	SELECT COUNT(DISTINCT sa.album_id) AS album_count
	FROM selected_albums AS sa 
),

total_matrix AS (
	SELECT 
		csu.user_count,
		csa.album_count,
		csu.user_count * csa.album_count AS total_count
	FROM count_selected_users AS csu
	CROSS JOIN count_selected_albums AS csa
), 

total_interactions AS (
	SELECT COUNT(DISTINCT ur.id) AS total_interactions
	FROM user_reviews AS ur
	WHERE ur.album_id IN (SELECT DISTINCT album_id FROM selected_albums)
	AND ur.username IN (SELECT DISTINCT username FROM selected_users)	
)

SELECT 
	tm.user_count, 
	tm.album_count,
	ti.total_interactions,
	tm.total_count AS possible_interactions,
	(100.0 * ti.total_interactions / tm.total_count) AS percent_coverage
FROM total_matrix AS tm
CROSS JOIN total_interactions AS ti;
	
SELECT COUNT(*)
FROM users;


