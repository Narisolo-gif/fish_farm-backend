

## 🐳 1. Commandes de base

À exécuter dans le dossier qui contient le `docker-compose.yml`.

### Démarrer les conteneurs

```powershell
docker compose up -d
```

### Démarrer + reconstruire les images

À utiliser après une modification du `Dockerfile`, des dépendances, etc.

```powershell
docker compose up -d --build
```

### Arrêter les conteneurs

```powershell
docker compose down
```

### Redémarrer

Très pratique après une modification de configuration :

```powershell
docker compose restart
```

### Voir l'état des conteneurs

```powershell
docker compose ps
```

---

# 🐍 2. Backend Django

Depuis :

```text
fish_farm-backend/
```

### Ouvrir un shell Django

```powershell
docker compose exec backend python manage.py shell
```

### Vérifier Django

```powershell
docker compose exec backend python manage.py check
```

### Créer les migrations

Après une modification de modèle :

```powershell
docker compose exec backend python manage.py makemigrations
```

### Appliquer les migrations

```powershell
docker compose exec backend python manage.py migrate
```

### Voir l'état des migrations

```powershell
docker compose exec backend python manage.py showmigrations
```

### Créer un superuser

```powershell
docker compose exec backend python manage.py createsuperuser
```

### Voir les logs backend

```powershell
docker compose logs backend
```

En continu :

```powershell
docker compose logs -f backend
```

---

# 🗄️ 3. PostgreSQL

### Entrer dans PostgreSQL

```powershell
docker compose exec db psql -U fish_farm -d fish_farm
```

Puis, dans PostgreSQL :

```sql
\dt
```

pour voir les tables.

```sql
\q
```

pour quitter.

### Voir les logs PostgreSQL

```powershell
docker compose logs db
```

---


# 🔎 5. Les logs : très important

Quand quelque chose ne fonctionne pas, commence généralement par :

```powershell
docker compose ps
```

puis :

```powershell
docker compose logs -f
```

Ou uniquement le service concerné :

```powershell
docker compose logs -f backend
```


Pour quitter les logs :

```text
Ctrl + C
```

---

# 🧹 6. Repartir proprement

### Arrêter et supprimer les conteneurs

```powershell
docker compose down
```

### Supprimer également les volumes

⚠️ **Attention : cela supprime notamment les données PostgreSQL du volume Docker.**

```powershell
docker compose down -v
```

Dans notre projet, on l'a déjà utilisé lorsque nous avons dû repartir proprement avec les migrations du modèle utilisateur.

**À ne pas utiliser machinalement.**

---

# 🧱 7. Quand modifier les dépendances

Par exemple, si tu modifies :

```text
requirements.txt
```

pour Django :

```powershell
docker compose up -d --build
```



# 📌 Ton workflow quotidien

Pour notre projet, je te conseille surtout de retenir ces commandes :

```powershell
# Démarrer
docker compose up -d

# Reconstruire + démarrer
docker compose up -d --build

# État
docker compose ps

# Redémarrer
docker compose restart

# Logs
docker compose logs -f

# Django
docker compose exec backend python manage.py check
docker compose exec backend python manage.py makemigrations
docker compose exec backend python manage.py migrate
```

Et surtout :

> **`restart`** → modification légère/configuration
> **`up -d --build`** → Dockerfile/dépendances
> **`down -v`** → remise à zéro des données Docker, donc à utiliser avec prudence.

Comme tes **backend et frontend ont chacun leur propre `docker-compose.yml`**, pense simplement à te placer dans le bon dossier avant d'exécuter `docker compose`.
