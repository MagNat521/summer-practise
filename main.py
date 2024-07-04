import os
import psycopg2
import time
import dotenv
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import NoSuchElementException, ElementClickInterceptedException, StaleElementReferenceException
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
import asyncio
import concurrent.futures

dotenv.load_dotenv() - заргужаем env 

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
) 

BOT_TOKEN = os.getenv('TELEGRAM_TOKEN')

def create_db_connection():
    dbname = os.getenv('DB_NAME')
    dbuser = os.getenv('DB_USER')
    dbpassword = os.getenv('DB_PASSWORD')
    dbhost = os.getenv('DB_HOST')
    dbport = os.getenv('DB_PORT')

    connection = psycopg2.connect(
        dbname=dbname,
        user=dbuser,
        password=dbpassword,
        host=dbhost,
        port=dbport
    )
    return connection

def add_vacancy_to_db(connection, company, title, location, salary, skills, link):
    with connection.cursor() as cursor:
        cursor.execute("""
        INSERT INTO vacancies (company, vacancy, location, salary, skills, link)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id;
        """, (company, title, location, salary, skills, link))
        connection.commit()
        return cursor.fetchone()[0]

def scrape_habr_vacancies(search_query):
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-software-rasterizer')
    chrome_options.add_argument('--disable-webgl')
    chrome_options.add_argument('--disable-web-security')
    chrome_options.add_argument('--disable-features=WebRtcHideLocalIpsWithMdns,WebContentsDelegate::CheckMediaAccessPermission')
    chrome_options.add_argument('--disable-extensions')
    chrome_options.add_argument('--disable-infobars')
    chrome_options.add_argument('--remote-debugging-port=9222')
    chrome_options.add_argument('--enable-features=NetworkService,NetworkServiceInProcess')
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_experimental_option('prefs', {
        'profile.managed_default_content_settings.images': 2,
        'disk-cache-size': 4096
    })

    driver = webdriver.Chrome(options=chrome_options)

    connection = create_db_connection()

    try:
        driver.get('https://career.habr.com')

        search_box = driver.find_element(By.CSS_SELECTOR, '.l-page-title__input')
        search_box.send_keys(search_query)
        search_box.send_keys(Keys.RETURN)

        time.sleep(1)

        while True:
            vacancy_cards = driver.find_elements(By.CLASS_NAME, 'vacancy-card__info')
            for card in vacancy_cards:
                try:
                    company = card.find_element(By.CLASS_NAME, 'vacancy-card__company-title').text
                except NoSuchElementException:
                    company = 'Компания не указана'

                title = card.find_element(By.CLASS_NAME, 'vacancy-card__title').text
                link = card.find_element(By.TAG_NAME, 'a').get_attribute('href')

                try:
                    location = card.find_element(By.CLASS_NAME, 'vacancy-card__meta').text
                except NoSuchElementException:
                    location = 'Местоположение не указано'

                try:
                    salary = card.find_element(By.CLASS_NAME, 'vacancy-card__salary').text
                except NoSuchElementException:
                    salary = 'ЗП не указана'

                try:
                    skills = card.find_element(By.CLASS_NAME, 'vacancy-card__skills').text
                except NoSuchElementException:
                    skills = 'Скиллы не указаны'

                vacancy_id = add_vacancy_to_db(connection, company, title, location, salary, skills, link)

                print(f'Компания: {company}\nВакансия: {title}\nСсылка: {link}\nМестоположение: {location}\nЗарплата: {salary}\nСкиллы: {skills}')

            try:
                next_button = driver.find_element(By.CSS_SELECTOR, 'a.button-comp--appearance-pagination-button[rel="next"]')
                driver.execute_script("arguments[0].scrollIntoView(true);", next_button)
                time.sleep(1)

                for _ in range(3):
                    try:
                        driver.execute_script("arguments[0].click();", next_button)
                        break
                    except StaleElementReferenceException:
                        next_button = driver.find_element(By.CSS_SELECTOR, 'a.button-comp--appearance-pagination-button[rel="next"]')
                        time.sleep(1)
                else:
                    break

                time.sleep(1)
            except (NoSuchElementException, ElementClickInterceptedException):
                break

    finally:
        driver.quit()
        connection.close()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Используйте /search <запрос>, чтобы искать вакансии.')

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    search_query = ' '.join(context.args)
    logging.info(f"Поиск запроса: {search_query}")
    if not search_query:
        await update.message.reply_text('Введите запрос после команды /search.')
        return

    connection = create_db_connection()
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM vacancies;")
        initial_count = cursor.fetchone()[0]
    connection.close()

    await update.message.reply_text(f'Ищу вакансии для: {search_query}')
    await execute_scrape_habr(search_query)
    await update.message.reply_text('Поиск завершен. Проверьте базу данных.')

    connection = create_db_connection()
    with connection.cursor() as cursor:
        cursor.execute("SELECT company, vacancy, location, salary, skills, link FROM vacancies WHERE id > %s ORDER BY id LIMIT 5;", (initial_count,))
        results = cursor.fetchall()
    connection.close()

    if not results:
        await update.message.reply_text('Новые вакансии не найдены.')
    else:
        await update.message.reply_text('Новые вакансии:')
        for result in results:
            await update.message.reply_text(f'Компания: {result[0]}\nВакансия: {result[1]}\nМестоположение: {result[2]}\nЗарплата: {result[3]}\nСкиллы: {result[4]}\nСсылка: {result[5]}\n')

async def execute_scrape_habr(query: str):
    loop = asyncio.get_event_loop()
    executor = concurrent.futures.ThreadPoolExecutor()
    await loop.run_in_executor(executor, scrape_habr_vacancies, query)

async def recent_vacancies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    connection = create_db_connection()
    with connection.cursor() as cursor:
        cursor.execute("SELECT company, vacancy, location, salary, skills, link FROM vacancies ORDER BY RANDOM() LIMIT 5;")
        results = cursor.fetchall()
    connection.close()

    if not results:
        await update.message.reply_text('Вакансии не найдены.')
    else:
        for result in results:
            await update.message.reply_text(f'Компания: {result[0]}\nВакансия: {result[1]}\nМестоположение: {result[2]}\nЗарплата: {result[3]}\nСкиллы: {result[4]}\nСсылка: {result[5]}\n')

async def count_vacancies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    connection = create_db_connection()
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM vacancies;")
        total_count = cursor.fetchone()[0]
    connection.close()
    await update.message.reply_text(f'Всего вакансий в базе данных: {total_count}')

async def schedule_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    buttons = [
        [
            InlineKeyboardButton("Неполный рабочий день", callback_data='part_time'),
            InlineKeyboardButton("Полный рабочий день", callback_data='full_time')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text('Выберите график работы:', reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    callback_query = update.callback_query
    callback_data = callback_query.data

    connection = create_db_connection()
    with connection.cursor() as cursor:
        if callback_data == 'part_time':
            cursor.execute("SELECT COUNT(*) FROM vacancies WHERE location ILIKE '%Неполный рабочий день%';")
        elif callback_data == 'full_time':
            cursor.execute("SELECT COUNT(*) FROM vacancies WHERE location ILIKE '%Полный рабочий день%';")
        count = cursor.fetchone()[0]
    connection.close()

    await callback_query.answer()
    await callback_query.edit_message_text(text=f'Вакансий с графиком "{callback_data}": {count}')

async def search_by_company_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    company_name = ' '.join(context.args)
    logging.info(f"Поиск вакансий по компании: {company_name}")
    if not company_name:
        await update.message.reply_text('Введите название компании после команды /search_company.')
        return

    connection = create_db_connection()
    with connection.cursor() as cursor:
        cursor.execute("SELECT company, vacancy, location, salary, skills, link FROM vacancies WHERE company ILIKE %s ORDER BY RANDOM() LIMIT 5;", (f"%{company_name}%",))
        results = cursor.fetchall()
    connection.close()

    if not results:
        await update.message.reply_text(f'Вакансии компании "{company_name}" не найдены.')
    else:
        for result in results:
            await update.message.reply_text(f'Компания: {result[0]}\nВакансия: {result[1]}\nМестоположение: {result[2]}\nЗарплата: {result[3]}\nСкиллы: {result[4]}\nСсылка: {result[5]}\n')

async def search_by_vacancy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    vacancy_name = ' '.join(context.args)
    logging.info(f"Поиск вакансий по названию: {vacancy_name}")
    if not vacancy_name:
        await update.message.reply_text('Введите название вакансии после команды /search_vacancy.')
        return

    connection = create_db_connection()
    with connection.cursor() as cursor:
        cursor.execute("SELECT company, vacancy, location, salary, skills, link FROM vacancies WHERE vacancy ILIKE %s ORDER BY RANDOM() LIMIT 5;", (f"%{vacancy_name}%",))
        results = cursor.fetchall()
    connection.close()

    if not results:
        await update.message.reply_text(f'Вакансии по запросу "{vacancy_name}" не найдены.')
    else:
        for result in results:
            await update.message.reply_text(f'Компания: {result[0]}\nВакансия: {result[1]}\nМестоположение: {result[2]}\nЗарплата: {result[3]}\nСкиллы: {result[4]}\nСсылка: {result[5]}\n')

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("recent", recent_vacancies))
    application.add_handler(CommandHandler("count", count_vacancies))
    application.add_handler(CommandHandler("grafic", schedule_filter))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(CommandHandler("search_company", search_by_company_command))
    application.add_handler(CommandHandler("search_vacancy", search_by_vacancy_command))

    application.run_polling()

if __name__ == '__main__':
    main()
