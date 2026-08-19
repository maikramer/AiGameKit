import { parseNumberAttr, type Parser } from '../../core';
import { WorldBorder } from './components';

export const worldBorderParser: Parser = ({ entity, element }) => {
  if (element.tagName.toLowerCase() !== 'worldborder') return;

  WorldBorder.radius[entity] = parseNumberAttr(
    element.attributes['radius'],
    600
  );
  WorldBorder.warnSeconds[entity] = parseNumberAttr(
    element.attributes['warn-seconds'],
    5
  );
  WorldBorder.margin[entity] = parseNumberAttr(
    element.attributes['margin'],
    24
  );
  WorldBorder.warnUntil[entity] = 0;
  WorldBorder.lastShownSecond[entity] = 0;
  WorldBorder.teleported[entity] = 0;
};
