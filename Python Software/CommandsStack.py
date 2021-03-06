#!/usr/bin/env python
# -*- coding: utf-8 -*- 

#VirToCut - Controlsoftware for a dynamical Plate-Saw-Machine
#Copyright (C) 2016  Benjamin Hirmer - hardy at virtoreal.net

#This program is free software: you can redistribute it and/or modify
#it under the terms of the GNU General Public License as published by
#the Free Software Foundation, either version 3 of the License, or
#(at your option) any later version.

#This program is distributed in the hope that it will be useful,
#but WITHOUT ANY WARRANTY; without even the implied warranty of
#MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#GNU General Public License for more details.

#You should have received a copy of the GNU General Public License
#along with this program.  If not, see <http://www.gnu.org/licenses/>.

#TODO
# Umkehrspiel auf Y und Z Achse berücksichtigen
# Beschleunigungen setzen für Hardware-Buttons
# Beschleunigungen in Marlin setzen + Abstand_Saegeblatt_zum_Materialanschlag mit Offset "druckbett"
# Schnittgeschwindigkeit automatisch interpretieren lassen beim Schneiden, rückfahrt immer mit maximaler Geschwindigkeit
# Netzteil automatisch an,- und abschalten (ControlGUI.py)
# Erzeugte Schneidvorlage verwerfen wenn Vorschub bewegt wird

safety_blade_distance = 2 # Abstand von Schneidmesser zum Material beim Vorschub wenn Säge entgegengesetzt schneidet

class CommandsStack: # Klasse zum senden von vordefinierten G-Code abläufen

    # Vorinitialisierte Werte von Privaten Variablen:
    _verbose = False

    def __init__ (self, verbose, tools, serialsession, scale, material, label_position, schneidvorlage_items, status_items, gpioner): # Übergibt Verbindungs-Parameter beim aufrufen der Klasse

        self._verbose = verbose # Verbose-Modus überladen
        self._serial = serialsession # Instanz von momentaner seriellen Verbindung
        self._scale = scale
        self._material = material
        self._label_position = label_position #Tupel mit den GTK.Labels zum darstellen der momentanen X/Y/Z Position
        self._schneidvorlage_items = schneidvorlage_items #Tupel mit allen für die Schneidvorlage notwendigen GTK Elemente
        self._status_items = status_items #Tupel mit GTK-Elementen für die Darstellung in der Statusanzeige
        self.gpioner = gpioner #Instanz des initialisierten GPIO-Ports des Raspberrys

        self.svprogress = False # Verarbeitung der Schneidvolage als deaktiviert initialisieren
        self.blockbuttons = False # Zugriff auf Hardware/Software Buttons prinzipiell erlauben
        self.feedsvalue = 0 # Gesamtvorschub einer erzeugten Interpreter-Instanz mit '0' initialisieren
        self.__cutvalue = False # Maximale Sägenposition bei entgegengesetzter Schneidrichtung als vollständig auszufahren ansehen

        self.tools = tools # Übergebe Tools Klasse
        self.load() # Lade schon einmal die aktuellen G-Codes

        self.cutting_template_editstate = False # Vorinitialisierter Zustand der Schneidvorlage
        self._schneivorlage_filepath = None # Vorinitialisierte Pfadangabe für Schneidvorlagen


    def load (self): # Lade momentane G-Codes und Einstellungen aus Yaml-Datei
        try: # Versuche G-Code Datei zu laden
            self._gcode = self.tools.yaml_load(self._verbose, ['Config'], 'GCode')
            self.checkmaxcut_value = self.checkmaxcut() # Prüfe ob die Säge entgegengesetzt schneidet
        except:
            self.tools.verbose(self._verbose, "Konnte GCode-Datei nicht laden, evtl exisitiert diese noch nicht")
            self._gcode = None

        try: # Versuche Einstellungen zu laden
            self._settings = self.tools.yaml_load(self._verbose, ['Config'], 'Einstellungen') #TODO ist das notwendig?
        except:
            self.tools.verbose(self._verbose, "Konnte Einstellungs-Datei nicht laden, evtl exisitiert diese noch nicht")
            self._settings = None


    def checkgcode (self, section): # Prüfe ob G-Code vorhandens
        if self._gcode:
            if section == 'ALL': # Prüfe ob für alle Arbeitsschritte G-Code angelegt worden ist
                if self.checkgcode('HOME') and self.checkgcode('VORHERIG') and self.checkgcode('NACHFOLGEND') and self.checkgcode('ANPRESSEN') and self.checkgcode('SCHNEIDEN') and self.checkgcode('VORSCHUB') and self.checkgcode('RUECKFAHRT') and self.checkgcode('FREIGEBEN'):
                    return True
                else:
                    return False
            elif self._gcode[section]: # Prüfe Arbeitsschritt ob G-Code angelegt wurde
                return True
            else:
                self.tools.verbose(self._verbose, "kein G-Code für diesen Arbeitsschritt '" + section + "' vorhanden, kann Befehl nicht ausführen. Bitte G-Code Satz unter 'Einstellungen' anlegen")
                return False
        else:
            self.tools.verbose(self._verbose, "keine G-Codes unter 'Einstellungen' angelegt")


    def checkvalue (self, gcode, value, placeholder, constant, section): # Ersetzt Platzhalter <placeholder>/<time_saw>/<time_vac> dem zugehörigen Wert
        gcode = gcode.replace('<time_saw>', str(self._settings['PDS']['Nachlaufzeit_Saege'])) # Ersetzt <time_saw> mit Wert aus Einstellungen
        gcode = gcode.replace('<time_vac>', str(self._settings['PDS']['Nachlaufzeit_Staubsauger'])) # Ersetzt <time_vac> mit Wert aus Einstellungen
        if constant:  # Wenn nur auf Konstanten geprüft werden soll, Platzhalter für <value> ignorieren
            return gcode
        else:
            if '<' + placeholder + '>' in gcode: # Prüfe ob Platzhalter im G-Code
                if value != None:
                    return gcode.replace('<' + placeholder + '>', str(value))
                else:
                    self.tools.verbose(self._verbose, "kein Distanzwert für diesen Arbeitsschritt '" + section + "' erhalten, wird aber in diesen Arbeitsschritt dringend benötigt!")
                    return False
            else:
                self.tools.verbose(self._verbose, "kein '<value>' Platzhalter für diesen Arbeitsschritt '" + section + "' gefunden, wird aber in diesen Arbeitsschritt dringend benötigt!")
                return False


    def checkmaxcut (self): # Prüfe ob Säge entgegengesetzt schneidet indem der Arbeitsschritt "Schneiden" auf den Platzhalter '<max_cut>' geprüft wird
        if self.checkgcode('SCHNEIDEN'): #Prüfe ob Arbeitsschritt angelegt wurde
            if any(x in self._gcode['SCHNEIDEN'] for x in ['<max_cut>']): #Prüfe Arbeitsschritt "Schneiden" auf Platzhalter
                self._status_items[2].set_reveal_child(True) #Zeige Togglebutton zum überschreiben der maximalen Sägenposition an um diese nicht vollständig ausfahren zu müssen
                return True # Gebe 'True' zurück falls vorhanden
            else:
                self._status_items[2].set_reveal_child(False) #Deaktiviere Togglebutton zum überschreiben der maximalen Sägenposition
                return False # andernfalls 'False'
        else:
            return False # Gebe 'False' zurück falls nicht angelegt


    def toggle_ms (self, force=False, deactivate=False): # Schaltet Möglichkeit ein/aus die momentane Sägenposition als maximal ausgefahren anzusehen
        status = self._status_items[3].get_active() # Prüfe welchen Status der ToggleButton hat
        if status and not deactivate: # Wenn eingeschaltet
            if force: # Überschreiben wird vom Anwender angefordert
                xvalue = float(self._label_position[0].get_text())
                if xvalue < self._settings['PDS']['Abstand_Saegeblatt_zum_Materialanschlag']: # Säge befindet sich noch im eingefahrenen Bereich
                    self.tools.verbose(self._verbose, "Säge ist noch eingefahren, warum Sägenposition als max. ausgefahren ansehen?", True)
                    self._status_items[3].set_active(False) # ToggleButton deaktivieren
                else:
                    self.__cutvalue = xvalue
            else: # andernfalls Überschreiben vom Anwender bestätigen lassen
                self.tools.infobar('QUESTION', 'Wirklich momentane Sägenposition als maximal ausgefahren ansehen? Auf Sägeblatt aufpassen!', 'YES/NO')
        elif deactivate:
            self._status_items[3].set_active(False) # ToggleButton deaktivieren
        else:
            self.tools.infobar('','','HIDE') # Falls Infobar bereits geöffnet, diese wieder deaktivieren


    def getmaterialthickness (self): # Holt die Auswahl der Materialstärke und gibt diese zurück
        self.materiallist = self._settings['VFM']['Vorhandene_Materialstaerken']
        selected = self._material.get_active_text() # Text der Materialauswahl
        for i in self.materiallist: # Durchlaufe alle Einträge und vergleiche mit der Auswahl
            if i[0] == selected: #Gebe Materialstärke zurück
                return i[1]
                break


    def BUTTON (self, buttontype, function, secfunction=False): # Funktion für das Drücken eines Buttons in der Software oder auf der Hardware
        try:
            scalevalue = float(self._scale.get_text()) # Hole Schrittweite
        except:
            self.tools.verbose(self._verbose, "Schrittweite noch nicht gesetzt, bitte korrigieren!")
            scalevalue = 0
        xvalue = float(self._label_position[0].get_text()) # Hole Position X-Achse
        yvalue = float(self._label_position[1].get_text()) # Hole Position Y-Achse
        if not self.blockbuttons and (xvalue != "N/V" or yvalue != "N/V"): # Prüfen ob die Buttons freigegeben sind
            if buttontype == "SW": # Software-Buttons
                if function == "GV": # Software-Button - Vorschub ganz vor
                    self.vorschub(2, 1000) #Code 2 = Benutzerausgeführt
                elif function == "V": # Software-Button - Vorschub vor
                    self.vorschub(2, scalevalue) #Code 2 = Benutzerausgeführt
                elif function == "Z": # Software-Button - Vorschub zurück
                    self.rueckfahrt(2, scalevalue) #Code 2 = Benutzerausgeführt
                elif function == "GZ": # Software-Button - Vorschub ganz zurück
                    self.rueckfahrt(2, 0) #Code 2 = Benutzerausgeführt
                elif function == "S" and self.svprogress and self.__confirmedstate == "SW": #Software-Button - "Schneiden" wurde im Programmmodus betätigt
                    if self.gcodeblock == 0:
                        self.sequenced_sending(1, 'SW') #Bestätige ersten G-Code Block zum abfertigen
                    elif self.gcodeblock > 0:
                        self.sequenced_sending(2, 'SW') #Bestätige ersten G-Code Block zum abfertigen
                elif function == "S": # Software-Button - Schneiden
                    self.schneiden(True, xvalue, self.getmaterialthickness())
                elif function == "H": # Software-Button - Homen
                    self.home(1) #Code 1 = Benutzerseitig ausgeführt
                elif function == "AP": # Software-Button - Anpressen
                    self.anpressen(2, self.getmaterialthickness()) #Code 2 = Benutzerausgeführt
                elif function == "AH": # Software-Button - Anheben
                    self.freigeben(2) #Code 2 = Benutzerausgeführt

            elif buttontype == "HW": # Hardware-Buttons
                if function == "VV": # Hardware-Button - Vorschub vor
                    self.vorschub(2, self._settings['HPDS']['Schrittweite_Vorschub']) #Code 2 = Benutzerausgeführt
                elif function == "VZ": # Hardware-Button - Vorschub zurück
                    if secfunction:
                        self.rueckfahrt(2, 0) #Code 2 = Benutzerausgeführt
                    else:
                        self.rueckfahrt(2, self._settings['HPDS']['Schrittweite_Vorschub']) #Code 2 = Benutzerausgeführt
                elif function == "SV": # Hardware-Button - Säge vor
                    if secfunction: # Hardware-Button - "Säge vor" lange gedrückt -> Säge ganz ausfahren
                        distance = self._settings['PDS']['Fahrbare_Strecke']
                    else: # Hardware-Button - "Säge vor" kurz gedrückt -> Säge um Schrittweite ausfahren
                        distance = self._settings['HPDS']['Schrittweite_Saege']
                    if xvalue == self._settings['PDS']['Fahrbare_Strecke']: # Säge ist bereits ganz ausgefahren
                        self.gpioner.ButtonPressed(0, 1, 'MovementError', 3) #Lasse Bewegungs-Buttons auf Bedienpaneel 3x blinken
                        cdist = 0
                    elif distance + xvalue > self._settings['PDS']['Fahrbare_Strecke']: #Prüfe ob Platz frei um vor zu fahren
                        cdist = self._settings['PDS']['Fahrbare_Strecke'] - xvalue
                    else:
                        cdist = distance
                    if cdist > 0:
                        self._serial.sending('G91\nG0 X%s\nG90' % str(cdist), 2)
                        self._label_position[0].set_text(str(xvalue + cdist))
                elif function == "SZ": # Hardware-Button - Säge zurück
                    if secfunction: # Hardware-Button - "Säge zurück" lange gedrückt -> Säge ganz einfahren
                        distance = self._settings['PDS']['Fahrbare_Strecke']
                    else: # Hardware-Button - "Säge zurück" kurz gedrückt -> Säge um Schrittweite einfahren
                        distance = self._settings['HPDS']['Schrittweite_Saege']
                    if xvalue == 0: # Säge ist bereits ganz eingefahren
                        self.gpioner.ButtonPressed(0, 1, 'MovementError', 3) #Lasse Bewegungs-Buttons auf Bedienpaneel 3x blinken
                        cdist = 0
                    elif xvalue < distance: #Prüfe ob Platz frei um zurück zu fahren
                        cdist = xvalue
                    else:
                        cdist = distance
                    if cdist > 0:
                        self._serial.sending('G91\nG0 X-%s\nG90' % str(cdist), 2)
                        self._label_position[0].set_text(str(xvalue - cdist))
                elif function == "S" and self.svprogress and self.__confirmedstate == "HW": #Hardware-Button - "Schneiden" wurde im Programmmodus betätigt
                    if self.gcodeblock == 0:
                        self.sequenced_sending(1, 'HW') #Bestätige ersten G-Code Block zum abfertigen
                    elif self.gcodeblock > 0:
                        self.sequenced_sending(2, 'HW') #Bestätige ersten G-Code Block zum abfertigen
                elif function == "S": # Hardware-Button - Schneiden
                    self.schneiden(True, 0, self.getmaterialthickness())
        else:
            self.tools.verbose(self._verbose, "Die Buttons werden momentan von der Software blockiert")


    def cutting_template_interpreter (self, cutting_template): # Interpretiert die Schneidvorlage und wandelt diesen in G-Code um
        # Hole die Momentan-Einstellungen die vor dem Programmstart anliegen
        materialthickness = self.getmaterialthickness() # Hole Auswahl der Materialdicke
        sawbladethickness = self._settings['PDS']['Schnittbreite'] # Hole Schnittbreite aus Einstellungen
        xvalue = float(self._label_position[0].get_text()) # Hole Position X-Achse
        yvalue = float(self._label_position[1].get_text()) # Hole Position Y-Achse
        zvalue = float(self._label_position[2].get_text()) # Hole Position Z-Achse
        self.__error = False # evtl. anstehende Fehler von vorherigen Schneidvorlage zurücksetzen
        self.gcodeblock = 0 # Setze abgearbeitete G-Code Blöcke auf 0
        self.feedsvalue = 0 # Setzt den Gesamtvorschub einer erzeugten Interpreter-Instanz auf 0

        self.gcodestack = [] # Liste aller zu erzeugenden Abläufe
        self.maxvlstack = [] # Liste aller maximalen Schnittweiten je G-Code Block
        neuezeile = '\n' # Kommando für eine neue Zeile
        rotated = False # Variable die den Urzustand definiert und nur zur Nachkontrolle verändert wird


        #TODO Sachen im Interpreter zu erledigen
        # - Aufteilung ändern wenn "Schnitte manuell bestätigen" und die dazugehörige Staubsauger/Sägenschaltung
        maxvalues = [] # Temporär gespeicherte maximale Schnittweite eines G-Code Blocks
        vorherig = self.vorherig() # 'Vorherig' in GCode-Vorlage einfügen wenn vorhanden
        nachfolgend = self.nachfolgend() # 'Nachfolgend' in GCode-Vorlage einfügen wenn vorhanden
        rowcount = 1 #Anfangen mit Zeilenummer 1
        if vorherig:
            gcodestring = vorherig + neuezeile
        else:
            gcodestring = None
        for l in cutting_template.split('\n'): # Lese Zeile für Zeile aus Schneidvorlage
            if not l == '': # Leerzeilen überspringen
                checkrow = self.tools.check_template(str(l))
                if checkrow: #Prüfe ob etwas auswertbares erzeugt wurde
                    if checkrow[0] == 0: # Tupel mit Parametern erhalten
                        if checkrow[1]: # Distanz für Vorschub liegt vor
                            bevor = gcodestring
                            vorschub = self.vorschub(False, checkrow[1] + sawbladethickness) # Übergebe Arbeitsschrittfunktion die Vorschubdistanz mit der Sägeblattbreite
                            if bevor == None and vorschub: # GCode "Vorschieben" zusammenfügen
                                gcodestring = vorschub + neuezeile
                            elif bevor and vorschub:
                                gcodestring = bevor + vorschub + neuezeile
                        if checkrow[2]: # Distanz für Schnittlänge liegt vor
                            bevor = gcodestring
                            anpressen = self.anpressen(False, materialthickness)
                            schneiden = self.schneiden(False, checkrow[2])
                            maxvalues.append(checkrow[2]) # Füge Schnittweite der temporären Schnittlängenliste an
                            freigeben = self.freigeben(False)
                            if bevor == None and anpressen and schneiden and freigeben:
                                gcodestring = anpressen + neuezeile + schneiden + neuezeile + freigeben + neuezeile
                            elif bevor and anpressen and schneiden and freigeben:
                                gcodestring = bevor + anpressen + neuezeile + schneiden + neuezeile + freigeben + neuezeile
                            #TODO self._settings['ZDS']['Schnitte_manuell_bestaetigen'] // Prüfen ob einzelne Schnitte durchgeführt werden müssen, falls ja, in Blöcke aufteilen
                    elif checkrow[0] == 1: # Aufforderung zum Drehen des Materials erhalten
                        rotated = True
                        self.gcodestack.append(gcodestring) # Füge ersten manuellen Arbeitsschritt (Material drehen) in GCode-Stack
                        self.maxvlstack.append(max(maxvalues)) # Füge maximale Schnittweite dieses G-Code Blocks in Liste hinzu
                        maxvalues = [] # Setzte temporäre Liste zurück
                        gcodestring = None
                    elif checkrow[0] == 2: # Aufforderung zur Aktion des Benutzers erhalten (Material richtig einlegen)
                        if not rotated: # Prüfe auf einen inkonsistenten Zustand der auftreten kann wenn die Schneivorlage falsch ist
                            self.tools.verbose(self._verbose, "Aufforderung zum drehen des Materials noch nicht erfolgt, Schneidvorlage scheint fehlerhaft zu sein")
                            self.__error = True #Teile Benutzer mit, das Probleme aufgetreten sind
                        else:
                            self.gcodestack.append(gcodestring)
                            self.maxvlstack.append(max(maxvalues)) # Füge maximale Schnittweite dieses G-Code Blocks in Liste hinzu
                            maxvalues = [] # Setzte temporäre Liste zurück
                            gcodestring = None
                else:
                    self.tools.verbose(self._verbose, "Nichts auswertbares in Schneidvorlage Zeile: '" + str(rowcount) + "' gefunden")
                    self.__error = True #Teile Benutzer mit, das Probleme aufgetreten sind
                rowcount += 1 #Fehlerangabenseite hochzählen

        if self.__error: #Fehler aufgetreten beim überprüfen der Schneidvorlage
            if self.__error == '1': #Fehlercode 1 / Typischer Fehler: Nur die Materialstärke wurde vergessen
                self.tools.verbose(self._verbose, "Schneidvorlage nicht akzeptiert, Sie haben kein Material ausgewählt", True)
            else:
                self.tools.verbose(self._verbose, "Schneidvorlage nicht akzeptiert, sie produziert Fehler - Debug aktivieren für Details", True)
        else:
            self.tools.infobar('INFO', "Schneidvorlage akzeptiert, Sie können die Schneidvorlage senden")
            if not gcodestring == '' and not rotated: # Interpreter braucht keinen Block erzeugen da nur ein Durchlauf
                self._schneidvorlage_items[7].set_sensitive(True) #Button "Schneidvorlage an Säge senden" aktivieren
                gcodestring = gcodestring + neuezeile + nachfolgend
                self.gcodestack.append(gcodestring) #Bringe G-Code in einheitliches System
                self.maxvlstack.append(max(maxvalues)) #Bringe maximale Schnittweiten in einheitliches System
                self.tools.verbose(self._verbose, "Der Interpreter hat folgenden G-Code generiert:\n" + gcodestring + "\nDabei hat er folgende maximale Schnittweite ermittelt:\n" + str(self.maxvlstack[0]))
            elif self.gcodestack:
                self.gcodestack.append(gcodestring + neuezeile + nachfolgend) #letzten gcodestring an Liste anhängen
                self.maxvlstack.append(max(maxvalues)) #letzten maxmimalen Schnittweiten-Wert an Liste anhängen
                self._schneidvorlage_items[7].set_sensitive(True) #Button "Schneidvorlage an Säge senden" aktivieren
                self.tools.verbose(self._verbose, "Der Interpreter hat folgende G-Code Abfolge generiert:\n" + str(self.gcodestack) + "\nDabei hat er folgende maximalen Schnittweiten ermittelt:\n" + str(self.maxvlstack))
            else:
                self.tools.verbose(self._verbose, "Schneidvorlage gibt vor nur horizontale Schnitte vorzulegen, will jedoch das Material drehen lassen, Schneidvorlage scheint fehlerhaft zu sein", True)


    def sequenced_sending (self, step, confirmed=False): #Sendet auf Befehl G-Code Sequenzen an den Arduino
        if step == 1 and not self.svprogress: #Ersten G-Code Block Abarbeiten
            self._status_items[0].set_reveal_child(True) #Zeige Fortschrittsanzeige
            self.svprogress = True #Aktiviere Fortschritt
            self._schneidvorlage_items[7].set_image(self._schneidvorlage_items[8]) #Wandle Button "Schneidvorlage an Säge senden" in "Sägevorgang abbrechen" um
            self._schneidvorlage_items[7].set_label("Sägevorgang abbrechen")
            if self.gcodeblock == 0 and not confirmed: #Sicherstellen ob am Anfang der G-Code Blöcke
                if self._settings['HPDS']['Start_anfordern']: #Prüfen ob Hardware-Button gedrückt werden muss
                    self.tools.infobar('INFO', "Bitte Vor-Ort an der Säge den Start bestätigen") #Weise Anwender darauf hin, das er den Beginn über die Hardware bestätigen muss
                    self.gpioner.ButtonBlink(23, 1, "ONOFF", True) #Lasse 'Schneiden' Button blinken bis Anwender darauf drückt
                    self.__confirmedstate = 'HW' #Bestätigungen sollten über die Hardware erfolgen
                else:
                    gcode = self.gcodestack[0].replace('<max_cut>', str(self.maxvlstack[0] + safety_blade_distance)) # Ersetzt <max_cut> mit maximaler Schnittweite des ersten G-Code Blocks wenn vorhanden
                    self._serial.sending(gcode, 0) #Sende direkt ersten Block an Maschine
                    self.gcodeblock += 1 #Abgearbeiteten Block hochzählen
                    self.blockbuttons = True #Alle Buttons sperren
                    self.__confirmedstate = 'SW' #Bestätigungen sollten über die Software erfolgen
            elif self.gcodeblock == 0 and confirmed == self.__confirmedstate:
                gcode = self.gcodestack[0].replace('<max_cut>', str(self.maxvlstack[0] + safety_blade_distance)) # Ersetzt <max_cut> mit maximaler Schnittweite des ersten G-Code Blocks wenn vorhanden
                self._serial.sending(gcode, 0) #Sende nach Bestätigung über Hardware ersten Block an Maschine
                self.gcodeblock += 1 #Abgearbeiteten Block hochzählen
            else:
                self.tools.verbose(self._verbose, "Es sollen G-Code Sequenzen gesendet werden, jedoch von der falschen Funktion -> Fehler im Programm", True)

        elif step == 2 and self.svprogress: #Nächsten Schritt abarbeiten
            if self.gcodeblock > 0: #Sicherstellen ob 1. G-Code Block schon abgearbeitet wurde
                if self._settings['HPDS']['Start_anfordern']: #Prüfen ob dies Vor-Ort geschehen muss
                    if self.__confirmedstate == 'HW' and not confirmed: #Fortschritt fordert ersten Block
                        self.tools.infobar('INFO', "Bitte Vor-Ort an der Säge den nächsten Schnitt bestätigen")
                        self.blockbuttons = False #Alle Buttons freigeben
                    elif self.__confirmedstate == confirmed: #Anwender bestätigt neuen Schnitt
                        gcode = self.gcodestack[self.gcodeblock].replace('<max_cut>', str(self.maxvlstack[self.gcodeblock] + safety_blade_distance)) # Ersetzt <max_cut> mit maximaler Schnittweite des jeweils nächsten G-Code Blocks wenn vorhanden
                        self._serial.sending(gcode, 0) #Sende jeweils nächsten G-Code Block
                        self.gcodeblock += 1 #Abgearbeiteten Block hochzählen
                        self.blockbuttons = True #Alle Buttons sperren
                    else:
                        self.tools.verbose(self._verbose, "Einstellungen fordern Vor-Ort Bestätigung, Programm hat diese im 1. Schritt jedoch nicht erhalten.", True)
                else:
                    if self.__confirmedstate == 'SW' and not confirmed: #Fortschritt fordert neuen Block
                        self.tools.infobar('INFO', "Bitte in der Software den nächsten Schnitt bestätigen")
                        self.blockbuttons = False #Alle Buttons freigeben
                    elif self.__confirmedstate == confirmed: #Anwender bestätigt neuen Schnitt
                        gcode = self.gcodestack[self.gcodeblock].replace('<max_cut>', str(self.maxvlstack[self.gcodeblock] + safety_blade_distance)) # Ersetzt <max_cut> mit maximaler Schnittweite des jeweils nächsten G-Code Blocks wenn vorhanden
                        self._serial.sending(gcode, 0) #Sende jeweils nächsten G-Code Block
                        self.gcodeblock += 1 #Abgearbeiteten Block hochzählen
                        self.blockbuttons = True #Alle Buttons sperren
            else:
                self.tools.verbose(self._verbose, "Erster G-Code Block noch nicht abgearbeitet, es wird jedoch schon der nächste aufgerufen -> Fehler im Programm", True)

        elif step == 3 and self.svprogress: #Alle Schritte abgearbeitet
            self.tools.infobar('INFO', "Schneidvorlage vollständig abgearbeitet!")
            self._schneidvorlage_items[7].set_image(self._schneidvorlage_items[9]) #Wandle Button "Sägevorgang abbrechen" in "Schneidvorlage an Säge senden" um
            self._schneidvorlage_items[7].set_label("Schneidvorlage an Säge senden")
            self.svprogress = False #Deaktiviere Fortschrittsanzeige
            self._status_items[0].set_reveal_child(False) #Verstecke Fortschrittsanzeige
            self.__confirmedstate = None
            self.gcodeblock = 0
            self._status_items[1].set_value(0) #GtkLevelBar für G-Code Block Fortschritt wieder auf 0 setzen
            self.blockbuttons = False #Alle Buttons freigeben

        else: # Falls Button "Sägevorgang abbrechen" gedrückt wird
            self.tools.verbose(self._verbose, "Sägevorgang wird abgebrochen", True)
            if self._serial.stopsending(): #Töte Sendethread und resette Arduino
                self.tools.verbose(self._verbose, "Sägevorgang wurde abgebrochen", True)
                self._schneidvorlage_items[7].set_image(self._schneidvorlage_items[9]) #Wandle Button "Sägevorgang abbrechen" in "Schneidvorlage an Säge senden" um
                self._schneidvorlage_items[7].set_label("Schneidvorlage an Säge senden")
                self.svprogress = False #Deaktiviere Fortschrittsanzeige
                self._status_items[0].set_reveal_child(False) #Verstecke Fortschrittsanzeige
                self.__confirmedstate = None
                self.gcodeblock = 0 #Setze abzuarbeitende G-Code Blöcke wieder auf 0
                self._status_items[1].set_value(0) #GtkLevelBar für G-Code Block Fortschritt wieder auf 0 setzen


    def get_transmission_status (self): #Gibt Anzahl G-Code Blöcke und abgearbeitet G-Code Blöcke zurück und passt G-Code Block Fortschritt an
        stackcount = len(self.gcodestack)
        percentage = self.gcodeblock / stackcount #Wert für G-Code Block Fortschrittsanzeige 0-1
        self._status_items[1].set_value(percentage) #Wert für GtkLevelBar
        return (stackcount, self.gcodeblock)


    def cutting_template_load (self, filepath): # Lade Schneidvorlage aus Datei in zur veranschaulichung in einen Textbuffer und übergebe sie den Interpreter der diese auf Gültigkeit überprüft
        if self.tools.check_file(self._verbose, filepath):
            self._schneivorlage_filepath = filepath
            self._schneidvorlage_items[0].set_text("Datei geladen: " + str(filepath)) # Stelle geladene Dateipfad in Schneidvorlage dar
            with open (filepath, 'r') as f:
                data = f.read()
            self._schneidvorlage_items[2].set_text(data) #Schneidvorlagen TextBuffer mit Dateiinhalt füllen
            self.cutting_template_interpreter(data) # Lasse Schneidvorlage vom Interpreter überprüfen
            self._schneidvorlage_items[6].set_sensitive(True) #Button "Bearbeiten" aktivieren
            self._schneidvorlage_items[4].set_sensitive(True) #Menüitem "Schneivorlage speichern" aktivieren
            self._schneidvorlage_items[5].set_sensitive(True) #Menüitem "Schneivorlage speichern unter" aktivieren


    def cutting_template_edit (self): # Editiere Schneidvorlage
        if self._schneivorlage_filepath:
            titlepath = str(self._schneivorlage_filepath)
        else:
            titlepath = "neue Schneidvorlage"
        if not self.cutting_template_editstate:
            self._schneidvorlage_items[0].set_text("Bearbeite: " + titlepath + '*') # Stelle geladene Dateipfad in Schneidvorlage dar
            self._schneidvorlage_items[7].set_sensitive(False) #Button "Schneidvorlage an Säge senden" deaktivieren
            self._schneidvorlage_items[1].set_sensitive(True) #TextView Widget aktivieren
            self._schneidvorlage_items[6].set_label('Fertig') #Button "Bearbeiten" in "Fertig" umbeschriften
            self.cutting_template_editstate = True
        else:
            self._schneidvorlage_items[0].set_text("Bearbeitet: " + titlepath + '*') # Stelle geladene Dateipfad in Schneidvorlage dar
            self._schneidvorlage_items[1].set_sensitive(False) #TextView Widget deaktivieren
            self._schneidvorlage_items[6].set_label('Bearbeiten') #Button "Fertig" in "Bearbeiten" umbeschriften
            self.cutting_template_interpreter(self.tools.fetch_textbuffer(self._verbose, self._schneidvorlage_items[2])) # Lasse bearbeitete Schneidvorlage vom Interpreter überprüfen
            self.cutting_template_editstate = False


    def cutting_template_save (self): # Geöffnete Schneidvorlage mit neuen Inhalt überschreiben
        if self._schneivorlage_filepath: # Wenn Datei geöffnet, neuen TextBuffer-Inhalt in Datei schreiben
            text = self.tools.fetch_textbuffer(self._verbose, self._schneidvorlage_items[2]) # Hole Text aus Schneidvorlagen Text-Buffer
            with open(self._schneivorlage_filepath, 'w') as f:
                f.write(text)
            self._schneidvorlage_items[0].set_text("Datei gespeichert: " + str(self._schneivorlage_filepath)) # Stelle gespeicherten Dateipfad in Schneidvorlage dar


    def cutting_template_save_as (self, filepath): # Geöffnete Schneidvorlage an neuen Ort abspeichern
        self._schneivorlage_filepath = self.tools.check_format(self._verbose, filepath, 1, '.vtc') #Pfad von Schneidvorlge
        self._schneidvorlage_items[4].set_sensitive(True) #Menüitem "Schneivorlage speichern" aktivieren
        text = self.tools.fetch_textbuffer(self._verbose, self._schneidvorlage_items[2]) # Hole Text aus Schneidvorlagen Text-Buffer
        status = self.tools.save_file(self._verbose, text, self._schneivorlage_filepath, ) #Speichere Datei
        self._schneidvorlage_items[0].set_text("Datei gespeichert: " + str(self._schneivorlage_filepath)) # Stelle gespeicherten Dateipfad in Schneidvorlage dar


    def cutting_template_new (self): # Erzeuge neue Schneidvolage
            self._schneidvorlage_items[0].set_text("neue Schneidvorlage") #Beschrifte die neue Schneidvorlage
            self._schneidvorlage_items[2].set_text('') #TextView leeren
            self._schneidvorlage_items[1].set_sensitive(True) #TextView Widget aktivieren
            self._schneidvorlage_items[6].set_sensitive(True) #Button "Bearbeiten" aktivieren
            self._schneidvorlage_items[6].set_label('Fertig') #Button "Bearbeiten" in "Fertig" umbeschriften
            self._schneidvorlage_items[7].set_sensitive(False) #Button "Schneidvorlage an Säge senden" deaktivieren
                #TODO Button "Schneidvorlage an Säge senden" nicht deaktivieren, wenn säge bereits sägt, würde sonst das Stoppen verhindern
            self._schneidvorlage_items[5].set_sensitive(True) #Menüitem "Schneivorlage speichern unter" aktivieren
            self.cutting_template_editstate = True


    #Folgende Funktionen kümmern sich um das Abarbeiten der Arbeitsschritte und prüfen auf Vollständigkeit der nötigen Variablen
    #Diese Funktionen können teilweise vom Benutzer direkt ausgeführt werden, Hauptsächlich jedoch vom Programminterpreter
    def home (self, user): #Arbeitsschritt - Homen
        if self.checkgcode('HOME'):
            if user:
                self._serial.sending(self._gcode['HOME'], user) #Wenn von Benutzer ausgelöst, direkt an die serielle Schnittstelle senden
            else:
                return self._gcode['HOME']
        else:
            self.__error = True #Teile Benutzer mit, das Probleme aufgetreten sind


    def vorherig (self): #Arbeitsschritt - Gcode der vor den Programmstart aufgerufen wird
        return self.checkvalue(self._gcode['VORHERIG'], None, 'value', True, 'VORHERIG') #G-Code zurück zum Programmgenerator


    def nachfolgend (self): #Arbeitsschritt - Gcode der nach den Programm aufgerufen wird
        return self.checkvalue(self._gcode['NACHFOLGEND'], None, 'value', True, 'NACHFOLGEND') #G-Code zurück zum Programmgenerator


    def anpressen (self, user, materialthickness): #Arbeitsschritt - Gcode der beim Anpressen erzeugt wird
        if self.checkgcode('ANPRESSEN'):
            if materialthickness != None: #Materialstärke sollte ausgewählt sein
                newgcode = self.checkvalue(self._gcode['ANPRESSEN'], float(self._settings['PDA']['Fahrbare_Strecke']) - float(materialthickness), 'value', False, 'ANPRESSEN') #Ersetze <value> mit den Wert der fahrbaren Strecke abz. der Materialdicke
                if newgcode:
                    if user:
                        self._serial.sending(newgcode, user) #Wenn von Benutzer ausgelöst, direkt an die serielle Schnittstelle senden
                    else:
                        return newgcode #Angepassten G-Code zurück zum Programmgenerator
                else:
                    self.__error = True #Teile Benutzer mit, das Probleme aufgetreten sind
            else:
                self.tools.verbose(self._verbose, "kann Material nicht arretieren, da keine Särke für das Material zum anpressen erhalten")
                self.__error = '1' #Teile Benutzer mit, das Probleme aufgetreten sind
        else:
            self.__error = True #Teile Benutzer mit, das Probleme aufgetreten sind


    def schneiden (self, user, distance, materialthickness=None): #Arbeitsschritt - Gcode der beim Schneiden erzeugt wird
        if self.checkgcode('SCHNEIDEN') and self.checkgcode('ANPRESSEN') and self.checkgcode('FREIGEBEN'):
            if distance < self._settings['PDS']['Fahrbare_Strecke']:
                xvalue = float(self._label_position[0].get_text()) #Hole Position Säge
                if user: #Bei manuellen 'schneiden' Material auch anpressen
                    if materialthickness != None: #Materialstärke sollte ausgewählt sein
                        if self.__cutvalue and xvalue == self.__cutvalue: # Prüfe ob ein manuelles "Überschreiben" der max. Sägenposition vom Anwender vorliegt und auch an richtiger Position steht
                            maxdistance = self.__cutvalue #<max_cut> als Ausgangspunkt akzeptieren
                            distance = xvalue #Säge komplett einfahren lassen
                        elif self.checkmaxcut_value and xvalue == self._settings['PDS']['Fahrbare_Strecke']: # Säge schneidet in rückwärts-Richtung und ist vollständig ausgefahren
                            maxdistance = self._settings['PDS']['Fahrbare_Strecke'] #momentane Position als Ausgangspunkt akzeptieren
                            distance = xvalue #Säge komplett einfahren lassen
                        elif not self.checkmaxcut_value and xvalue <= 0: # Säge schneidet in vorwärts-Richtung und befindet sich im Schutzbereich
                            maxdistance = self._settings['PDS']['Fahrbare_Strecke'] #Platzhalter hier eigentlich nicht nötig, bei Verwendung dennoch belegen
                            distance = self._settings['PDS']['Fahrbare_Strecke'] #Säge komplett ausfahren lassen
                        else: # Säge ist falsch positioniert
                            self.tools.verbose(self._verbose, "Säge läuft Gefahr vom Vorschub beschädigt zu werden, Aktion verweigert!", True)
                            if self.checkmaxcut_value:
                                self.toggle_ms(False, True) # Deaktiviere max. Sägenposition "Überschreiben" Togglebutton
                            return False # Funktion verlassen
                        try:
                            gcode = self.checkvalue(self._gcode['SCHNEIDEN'], distance, 'value', False, 'SCHNEIDEN') #Hänge angepassten G-Code für das 'Schneiden' an
                            gcode = self.checkvalue(gcode, maxdistance, 'max_cut', False, 'SCHNEIDEN') #Hänge angepassten G-Code für das 'Schneiden' an

                            newgcode = self.vorschub(False, self._settings['PDS']['Schnittbreite']) #Schiebe Material um eine Sägeblattbreite nach vorn
                            newgcode = newgcode + "\n" + self.anpressen(False, materialthickness) #Hole 'Anpressen' G-Code
                            newgcode = newgcode + "\n" + gcode #Optimierten 'Schneiden' G-Code einsetzen
                            newgcode = newgcode + "\n" + self.freigeben(False) #Hänge 'Freigeben' G-Code an
                            self._serial.sending(newgcode, user) #Anpressen + Schneiden + Freigeben an den seriellen Port schicken
                        except:
                            self.tools.verbose(self._verbose, "beim Versuch die Säge zu Bewegen ist ein Fehler aufgetreten", True)
                    else:
                        self.tools.verbose(self._verbose, "kann keinen Schneiddurchlauf starten, da keine Särke für das Material zum anpressen erhalten", True)
                else: #Bei automatischen 'schneiden'
                    newgcode = self.checkvalue(self._gcode['SCHNEIDEN'], distance, 'value', False, 'SCHNEIDEN')
                    if newgcode:
                        return newgcode #Angepassten G-Code zurück zum Programmgenerator
                    else:
                        self.__error = True #Teile Benutzer mit, das Probleme aufgetreten sind
            else:
                self.tools.verbose(self._verbose, "Schnittlänge überschreitet die Eingestellte fahrbare Stecke der Säge")
                self.__error = True #Teile Benutzer mit, das Probleme aufgetreten sind
        else:
            self.__error = True #Teile Benutzer mit, das Probleme aufgetreten sind


    def vorschub (self, user, distance): #Arbeitsschritt - Gcode der beim Vorschub erzeugt wird
        if user: #Bei manuellem Vorschub
            if not 'N/V' in self._label_position[3].get_text():
                xvalue = float(self._label_position[0].get_text())
                if self.__cutvalue: # Prüfe ob ein manuelles "Überschreiben" der max. Sägenposition vom Anwender vorliegt
                    if xvalue == self.__cutvalue: # Befindet sich Säge an der Position zum Zeitpunkt der Aktivierung, manuelle "maximale" Sägendistanz beibehalten
                        maxdistance = self.__cutvalue
                    else: # Ansonsten den ToggleButton deaktivieren und max. Sägenposition wieder auf voll ausgefahren definieren
                        self.toggle_ms(False, True) # Deaktiviere max. Sägenposition "Überschreiben" Togglebutton
                        maxdistance = self._settings['PDS']['Fahrbare_Strecke']
                else: # Ansonsten als vollständig auszufahren ansehen
                    maxdistance = self._settings['PDS']['Fahrbare_Strecke']
                minxdistance = self._settings['PDS']['Abstand_Saegeblatt_zum_Materialanschlag']
                if not self.svprogress: #Wenn kein Programm läuft
                    if self.checkmaxcut_value: #Prüfe ob Säge entgegengesetzt schneidet
                        max_cut = maxdistance # Säge muss ganz ausgefahren sein
                        errortext = "Säge muss vollständig ausgefahren sein"
                    else:
                        max_cut = False #Säge muss eingeparkt sein
                        errortext = "Säge muss eingefahren sein"
                else: #Falls programm läuft
                    if self.checkmaxcut_value: #Prüfe ob Säge entgegengesetzt schneidet
                        max_cut = self.maxvlstack[self.gcodeblock] #Die maximale Plattenbreite dem aktiven Block entnehmen
                        errortext = "Säge muss mindestens " + str(max_cut) + "mm ausgefahren sein"
                    else:
                        max_cut = False #Säge muss eingeparkt sein
                        errortext = "Säge muss eingefahren sein"
                if (xvalue < minxdistance and not max_cut) or (xvalue >= max_cut and max_cut): #Prüfe ob beim Vorschub das Sägeblatt nicht beschädigt wird
                    if distance > 999: #Vorschub nach ganz vorne
                            distance = self._settings['PDV']['Fahrbare_Strecke'] - float(self._label_position[3].get_text()) #Bilde restliche Distanz aus momentaner Position und eingestellter fahrbaren Strecke
                            self._serial.sending('G91\nG0 Y' + str(distance) + '\nG90\nM114', user) #Vorschub an den seriellen Port senden
                            self._label_position[1].set_text(str(float(self._label_position[1].get_text()) + distance)) #Y-Distanz hochzählen
                            self._label_position[3].set_text(str(self._settings['PDV']['Fahrbare_Strecke'])) #Absolute Y-Distanz hochzählen
                    else:
                        if distance + float(self._label_position[3].get_text()) <= self._settings['PDV']['Fahrbare_Strecke']:
                            self._serial.sending('G91\nG0 Y' + str(distance) + '\nG90', user) #Vorschub an den seriellen Port
                            self._label_position[1].set_text(str(float(self._label_position[1].get_text()) + distance)) #Y-Distanz hochzählen
                            self._label_position[3].set_text(str(float(self._label_position[3].get_text()) + distance)) #Absolute Y-Distanz hochzählen
                        else:
                            self.tools.verbose(self._verbose, "Vorschubdistanz überschreitet die eingestellte fahrbare Stecke des Vorschubs", True)
                            self.gpioner.ButtonPressed(0, 1, 'MovementError', 3) #Lasse Bewegungs-Buttons auf Bedienpaneel 3x blinken
                else:
                    self.tools.verbose(self._verbose, "Vorschub nicht möglich, " + errortext, True)
            else:
                self.tools.verbose(self._verbose, "keine absolute Position des Vorschubs vorhanden, bitte Maschine vorher 'homen'!", True)
        else: #Bei automatischen Vorschub
            if distance + self.feedsvalue + float(self._label_position[3].get_text()) <= self._settings['PDV']['Fahrbare_Strecke']:
                if self.checkgcode('VORSCHUB'):
                    newgcode = self.checkvalue(self._gcode['VORSCHUB'], distance, 'value', False, 'VORSCHUB') #G-Code zurück zum Programmgenerator
                    if newgcode:
                        return newgcode #Angepassten G-Code zurück zum Programmgenerator
                    else:
                        self.__error = True #Teile Benutzer mit, das Probleme aufgetreten sind
                else:
                    self.__error = True #Teile Benutzer mit, das Probleme aufgetreten sind
            else:
                self.tools.verbose(self._verbose, "Vorschubdistanz überschreitet die eingestellte fahrbare Stecke des Vorschubs")
                self.__error = True #Teile Benutzer mit, das Probleme aufgetreten sind
            self.feedsvalue += distance # Addiere Vorschubdistanz auf momentane Interpreter-Instanz


    def rueckfahrt (self, user, distance): #Arbeitsschritt - G-Gode der bei der Rückfahrt erzeugt wird
        if user:
            if not 'N/V' in self._label_position[3].get_text():
                if distance == 0: #Vorschub ganz zurück
                    distance = self._label_position[3].get_text() #Hole absolut zurück gelegte Y-Stecke und fahre danach zu '0'
                    self._serial.sending('G92 Y' + distance + '\nG0 Y0', user) #Rückfahrt an den seriellen Port senden
                    self._label_position[1].set_text('0') #Y-Distanz auf '0' setzen
                    self._label_position[3].set_text('0') #Absolute Y-Distanz auf '0' setzen
                else:
                    if float(self._label_position[1].get_text()) == 0 and float(self._label_position[3].get_text()) > 0: #Ausgangspunkt von relativen Vorschub erreicht
                        if float(self._label_position[3].get_text()) >= distance: #Die definierte Distanz passt noch in den zurückzulegenden Weg
                            self._serial.sending('G92 Y' + str(distance) + '\nG0 Y0', user) #Rückfahrt an den seriellen Port senden
                            self._label_position[3].set_text(str(float(self._label_position[3].get_text()) - distance)) #Absolute Y-Distanz herunterzählen
                        elif float(self._label_position[3].get_text()) < distance: #Die definierte Distanz passt nicht mehr in den zurückzulegenden Weg, Reststrecke wird ermittelt und gesendet
                            self._serial.sending('G92 Y' + self._label_position[3].get_text() + '\nG0 Y0', user) #Rückfahrt an den seriellen Port senden
                            self._label_position[3].set_text('0') #Absolute Y-Distanz auf '0' setzen
                            self.gpioner.ButtonPressed(0, 1, 'MovementError', 2) #Lasse Bewegungs-Buttons auf Bedienpaneel 2x blinken um Anwender zu signalisieren das er nun mit dem Vorschub auf absolut '0' steht
                    elif float(self._label_position[1].get_text()) < distance: #Die relative Distanz passt nicht mehr in die zurückzulegende Strecke, Reststrecke wird ermittelt und gesendet
                        self._serial.sending('G91\nG0 Y-' + self._label_position[1].get_text() + '\nG90', user) #Rückfahrt an den seriellen Port senden
                        self._label_position[1].set_text('0') #Y-Distanz auf '0' setzen
                        self._label_position[3].set_text(str(float(self._label_position[3].get_text()) - float(self._label_position[1].get_text()))) #Absolute Y-Distanz herunterzählen
                        self.gpioner.ButtonPressed(0, 1, 'MovementError', 1) #Lasse Bewegungs-Buttons auf Bedienpaneel 1x blinken um Anwender zu signalisieren das er nun am Ausgangspunkt des Vorschubs ist
                    elif float(self._label_position[1].get_text()) >= distance:
                        self._serial.sending('G91\nG0 Y-' + str(distance) + '\nG90', user) #Rückfahrt an den seriellen Port senden
                        self._label_position[1].set_text(str(float(self._label_position[1].get_text()) - distance)) #Y-Distanz herunterzählen
                        self._label_position[3].set_text(str(float(self._label_position[3].get_text()) - distance)) #Absolute Y-Distanz herunterzählen
            else:
                self.tools.verbose(self._verbose, "keine absolute Position des Vorschubs vorhanden, bitte Maschine vorher 'homen'!", True)
        else:
            if self.checkgcode('RUECKFAHRT'):
                newgcode = self.checkvalue(self._gcode['RUECKFAHRT'], distance, 'value', False, 'RUECKFAHRT') #G-Code zurück zum Programmgenerator
                if newgcode:
                    return newgcode #Angepassten G-Code zurück zum Programmgenerator
                else:
                    self.__error = True #Teile Benutzer mit, das Probleme aufgetreten sind
            else:
                self.__error = True #Teile Benutzer mit, das Probleme aufgetreten sind


    def freigeben (self, user): #Arbeitsschritt - Gcode der bei der beim Freigeben erzeugt wird
        if self.checkgcode('FREIGEBEN'):
            newgcode = self.checkvalue(self._gcode['FREIGEBEN'], 0, 'value', False, 'FREIGEBEN')
            if newgcode:
                if user:
                    self._serial.sending(newgcode, user) #Wenn von Benutzer ausgelöst, direkt an die serielle Schnittstelle senden
                else:
                    return newgcode #Angepassten G-Code zurück zum Programmgenerator
            else:
                self.__error = True #Teile Benutzer mit, das Probleme aufgetreten sind
        else:
            self.__error = True #Teile Benutzer mit, das Probleme aufgetreten sind
