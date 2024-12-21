from flask import Flask, request, render_template, redirect, url_for, session, flash
import pronotepy
from pronotepy.ent import ac_reims
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib import dates as mdates
from datetime import datetime
import os
import io
import base64

app = Flask(__name__)
app.secret_key = "your_secret_key"  # Nécessaire pour gérer la session utilisateur

# Connexion à Pronote
def connect_to_pronote(username, password):
    try:
        client = pronotepy.Client(
            'https://0101016a.index-education.net/pronote/eleve.html',
            username=username,
            password=password,
            account_pin="1111",
            ent=ac_reims,
            device_name="pronotepy"
        )
        return client if client.logged_in else None
    except Exception as e:
        print(f"Erreur de connexion : {e}")
        return None

# Charger les données des grades
def load_grades(client):
    grades_data = []
    for period in client.periods:
        for grade in period.grades:
            note_en_cours = grade.to_dict({"comment", 'subject', 'id', "max", "min", 'default_out_of', 'is_out_of_20'})
            note_en_cours["subject"] = grade.subject.name
            note_en_cours["date"] = grade.date
            grades_data.append(note_en_cours)
    return grades_data

# Calculer la moyenne pour chaque note et mettre à jour les dates des bonus/facultatifs
def calculate_moving_average(df):
    df['grade'] = df['grade'].str.replace(",", ".").astype(float)
    df['normalized_grade'] = df['grade'].astype(float) * 20 / df['out_of'].astype(float)
    df['average'] = df['average'].str.replace(",", ".").astype(float)
    df['normalized_grade_average'] = df['average'].astype(float) * 20 / df['out_of'].astype(float)
    df.loc[df['out_of'] == 20, 'normalized_grade'] = df['grade']

    # Trier les lignes avec is_optionnal=False et is_bonus=False par date
    df_no_bonus_optional = df[(df['is_optionnal'] == False) & (df['is_bonus'] == False)].sort_values(by='date', ascending=True)

    # Trier les autres lignes par normalized_grade en ordre décroissant
    df_other = df[(df['is_optionnal'] != False) | (df['is_bonus'] != False)].sort_values(by='normalized_grade', ascending=False)

    # Trouver la dernière date des notes obligatoires (non facultatives et non bonus)
    if not df_no_bonus_optional.empty:
        last_mandatory_date = df_no_bonus_optional['date'].max()
    else:
        last_mandatory_date = pd.Timestamp.now()  # Si aucune note obligatoire n'existe, utilisez aujourd'hui.

    # Ajouter 1 jour à la dernière date obligatoire pour les notes facultatives ou bonus
    df_other['date'] = df_other['date'].apply(
        lambda d: last_mandatory_date + pd.Timedelta(days=1) if pd.notnull(d) else d
    )

    # Fusionner les deux DataFrames
    return pd.concat([df_no_bonus_optional, df_other])

def calculer(df, average):
    liste_moyennes, liste_dates = [], []
    moyenne = 0
    coefficient_moyen = 0
    num = 0

    for _, row in df.iterrows():
        if average:
            grade = float(row['normalized_grade_average'])
            raw_grade = float(row['average'])
        else:
            grade = float(row['normalized_grade'])
            raw_grade = float(row['grade'])
        out_of = float(row['out_of'])
        coefficient = float(row['coefficient'])
        is_bonus = row['is_bonus']
        is_optionnal = row['is_optionnal']

        moyenne_note = out_of / 2

        if is_bonus and raw_grade > moyenne_note:
            bonus = 20 * (raw_grade - moyenne_note) * coefficient / coefficient_moyen
            moyenne += bonus
            continue

        if is_optionnal and grade < moyenne:
            continue

        coefficient_moyen += out_of * coefficient
        num += coefficient * raw_grade
        moyenne = 20 * num / coefficient_moyen
        liste_moyennes.append(moyenne)
        liste_dates.append(row['date'])
    
    return liste_moyennes, liste_dates

# Générer et retourner une image graphique en base64
def generate_plot(df):
    liste_moyennes, liste_dates = calculer(df, False)
    liste_moyennes_moyen, liste_dates = calculer(df, True)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(liste_dates, liste_moyennes, marker='o', color='b', label='Moyenne élève')
    ax.axhline(y=10, color='gray', linestyle='--', label='Moyenne')
    ax.plot(liste_dates, liste_moyennes_moyen, marker='o', color='r', label='Moyenne de classe')

    ax.set_ylim(0, 20)
    ax.set_xlabel('Date')
    ax.set_ylabel('Moyenne')
    ax.set_title('Moyenne en fonction du temps')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d-%m-%Y'))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=5))
    plt.xticks(rotation=45)
    ax.legend()

    # Sauvegarder le graphique dans un buffer
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    # Encoder le graphique en base64 pour l'afficher dans HTML
    encoded_image = base64.b64encode(buf.read()).decode("utf-8")
    buf.close()
    return encoded_image

@app.route("/", methods=["GET", "POST"])
def main():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        client = connect_to_pronote(username, password)

        if client:
            session["logged_in"] = True
            session["username"] = username
            session["password"] = password

            # Charger les notes
            grades_data = load_grades(client)
            df = pd.DataFrame(grades_data)
            df = df[df['grade'] != "NonNote"]
            df['date'] = pd.to_datetime(df['date'])

            # Sauvegarder les données dans la session
            session["grades_data"] = df.to_dict("records")
            return redirect(url_for("dashboard"))
        else:
            flash("Échec de la connexion. Vérifiez vos identifiants.", "error")
            return redirect(url_for("main"))

    return render_template("login.html")

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("main"))

    df = pd.DataFrame(session["grades_data"])

    selected_matiere = request.form.get("matiere", "Toutes")
    if selected_matiere != "Toutes":
        df = df[df['subject'] == selected_matiere]

    df_sorted = calculate_moving_average(df)
    plot_url = generate_plot(df_sorted)

    matieres = sorted(df['subject'].unique())
    return render_template("dashboard.html", matieres=matieres, plot_url=plot_url)

@app.route("/logout")
def logout():
    session.clear()
    flash("Vous avez été déconnecté.", "success")
    return redirect(url_for("main"))

if __name__ == "__main__":
    app.run(debug=True)
